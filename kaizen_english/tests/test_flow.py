"""End-to-end tests of the learning engine against a real SQLite file (no Telegram needed).

Run from the kaizen_english folder:   python -m unittest discover -s tests -v
"""
import os
import random
import sys
import tempfile
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import content  # noqa: E402
from config import Config  # noqa: E402
from content import texts  # noqa: E402
from database.db import Database  # noqa: E402
from database.repository import Repository  # noqa: E402
from services import exercises, srs  # noqa: E402
from services.learning import Learning  # noqa: E402

UID = 42


class FakeClock:
    def __init__(self, today: date):
        self._today = today

    def today(self) -> date:
        return self._today

    def now(self):
        from datetime import datetime, timezone
        return datetime.combine(self._today, datetime.min.time(), tzinfo=timezone.utc).replace(hour=19, minute=30)

    def advance(self, days=1):
        self._today += timedelta(days=days)


def make_cfg(path: str) -> Config:
    return Config(bot_token="x", db_path=path, timezone="UTC", log_level="INFO")


class Env:
    """A bot instance bound to a DB file; can be 'restarted' by building a new Env on the same file."""

    def __init__(self, path, clock=None, seed=1):
        self.db = Database(path)
        self.db.migrate()
        self.repo = Repository(self.db)
        self.clock = clock or FakeClock(date(2026, 3, 2))
        self.L = Learning(self.repo, self.clock, make_cfg(path), random.Random(seed))
        self.L.ensure_user(UID, "u", "User")

    # --- helpers that behave like a user tapping buttons
    @staticmethod
    def buttons(screen):
        return [b for row in screen.rows for b in row]

    def act_of(self, screen):
        for b in self.buttons(screen):
            if b.data.startswith("a:"):
                _, aid, rev, _ = b.data.split(":")
                return self.repo.get_activity(int(aid)), int(rev)
        raise AssertionError("no activity buttons on screen: " + screen.text)

    def press(self, screen, op):
        act, rev = self.act_of(screen)
        return self.L.handle_action(UID, act["id"], rev, op)

    def answer(self, screen, correct=True):
        act, _ = self.act_of(screen)
        ex = act["state"]["ex"]
        idx = ex["answer"] if correct else next(
            i for i in range(4) if i != ex["answer"] and i not in act["state"]["wrong"])
        return self.press(screen, f"o{idx}")

    def last(self, resp):
        return resp.edit or resp.send[-1]

    def pick_level(self, level="A2"):
        resp = self.L.start_daily(UID)
        assert any(b.data.startswith("lvl:") for b in self.buttons(resp.send[0]))
        return self.L.choose_level(UID, level, onboarding=True)

    def finish_new_word(self, screen):
        """card -> exercise -> correct answer -> continue. Returns the Response after 'Continue'."""
        r = self.press(screen, "go")
        r = self.answer(self.last(r))
        return self.press(self.last(r), "nx")

    def play_daily(self, correct=True):
        resp = self.L.start_daily(UID)
        screen = resp.send[0]
        guard = 0
        while True:
            guard += 1
            assert guard < 40
            act, _ = self.act_of(screen)
            phase = act["state"]["phase"]
            if phase == "card":
                screen = self.last(self.press(screen, "go"))
                continue
            r = self.answer(screen, correct)
            screen = self.last(r)
            act, _ = self.act_of(screen)
            if act["state"]["phase"] != "answered":     # wrong first try -> retry or reveal
                r = self.answer(screen, correct)
                screen = self.last(r)
            r = self.press(screen, "nx")
            if r.send and not any(b.data.startswith("a:") for b in self.buttons(r.send[0])):
                return r.send[0]                         # completion screen
            screen = r.send[0]


class VocabularyTests(unittest.TestCase):
    def test_all_cards_valid_and_enough_words(self):
        content.validate(content.WORDS)
        self.assertGreaterEqual(len(content.WORDS), 30)
        for lv in ("A2", "B1", "B2"):
            self.assertGreaterEqual(len([w for w in content.WORDS if w["level"] == lv]), 8)

    def test_exercises_are_related_and_shuffled(self):
        rng = random.Random(0)
        positions = set()
        for w in content.WORDS:
            for t in exercises.EX_TYPES:
                ex = exercises.build_exercise(w, t, rng)
                self.assertEqual(len(ex["options"]), 4)
                self.assertEqual(len(set(ex["options"])), 4)
                right = w["ru_short"] if t == "translation" else w["word"]
                self.assertEqual(ex["options"][ex["answer"]], right)
                positions.add(ex["answer"])
        self.assertEqual(positions, {0, 1, 2, 3})


class SrsTests(unittest.TestCase):
    def test_interval_ladder(self):
        today = date(2026, 3, 2)
        rec = srs.new_word_record(today, True, "cloze")
        self.assertEqual(rec["next_review"], "2026-03-03")           # +1 day
        gaps = []
        for _ in range(4):
            due = date.fromisoformat(rec["next_review"])
            rec = srs.apply_answer(rec, True, due)
            gaps.append((date.fromisoformat(rec["next_review"]) - due).days)
        self.assertEqual(gaps, [3, 7, 14, 30])
        self.assertEqual(rec["stage"], 4)

    def test_wrong_answer_schedules_earlier(self):
        today = date(2026, 3, 10)
        rec = dict(srs.new_word_record(date(2026, 3, 2), True, None), stage=3, next_review="2026-03-10")
        out = srs.apply_answer(rec, False, today)
        self.assertEqual(out["stage"], 2)
        self.assertEqual(out["next_review"], "2026-03-11")
        self.assertEqual(out["incorrect_count"], 1)

    def test_extra_practice_does_not_boost_interval(self):
        today = date(2026, 3, 2)
        rec = srs.new_word_record(today, True, None)                  # due tomorrow
        out = srs.apply_answer(rec, True, today)
        self.assertEqual((out["stage"], out["next_review"]), (0, rec["next_review"]))
        out = srs.apply_answer(rec, False, today)                     # mistake -> not later than tomorrow
        self.assertEqual(out["next_review"], "2026-03-03")

    def test_streak(self):
        t = date(2026, 3, 10)
        self.assertEqual(srs.streak([t, t - timedelta(1), t - timedelta(2)], t), 3)
        self.assertEqual(srs.streak([t - timedelta(1)], t), 1)        # yesterday keeps the streak alive
        self.assertEqual(srs.streak([t - timedelta(3)], t), 0)


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "k.db")
        self.env = Env(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_onboarding_then_card(self):
        r = self.env.pick_level("A2")
        card = r.send[0]
        self.assertIn("Новое слово 1/2", card.text)
        self.assertEqual([b.text for b in self.env.buttons(card)], ["✍️ К заданию"])

    def test_daily_limit_and_completion_message(self):
        e = self.env
        e.pick_level()
        done = e.play_daily()
        self.assertEqual(done.text, texts.DAILY_DONE)
        self.assertEqual([b.text for b in e.buttons(done)], ["🧠 Дополнительные задания"])
        self.assertEqual(len(e.repo.all_words(UID)), 2)
        for _ in range(3):                                            # pressing /today again never gives new words
            again = e.L.start_daily(UID)
            self.assertIn("уже пройден", again.send[0].text)
        self.assertEqual(len(e.repo.all_words(UID)), 2)
        # a restart does not bypass the limit either
        e2 = Env(self.path, clock=e.clock)
        self.assertIn("уже пройден", e2.L.start_daily(UID).send[0].text)
        self.assertEqual(len(e2.repo.all_words(UID)), 2)

    def test_daily_target_three(self):
        e = self.env
        e.pick_level()
        e.repo.update_settings(UID, daily_target=3)
        e.repo.reset_progress(UID)                                    # drop today's 2-word lesson created by pick_level
        e.play_daily()
        self.assertEqual(len(e.repo.all_words(UID)), 3)

    def test_continue_only_after_exercise(self):
        e = self.env
        card = e.pick_level().send[0]
        self.assertNotIn("▶️ Продолжить", [b.text for b in e.buttons(card)])
        r = e.press(card, "go")
        ex_screen = r.send[0]
        texts_ = [b.text for b in e.buttons(ex_screen)]
        self.assertNotIn("▶️ Продолжить", texts_)
        # trying to force "next" before answering is rejected
        r = e.press(ex_screen, "nx")
        self.assertTrue(r.toast)
        self.assertEqual(len(e.repo.all_words(UID)), 0)

    def test_wrong_answer_retry_then_reveal(self):
        e = self.env
        card = e.pick_level().send[0]
        scr = e.last(e.press(card, "go"))
        act, _ = e.act_of(scr)
        ex = act["state"]["ex"]
        wrong = next(i for i in range(4) if i != ex["answer"])
        r = e.press(scr, f"o{wrong}")                                 # 1st wrong: explanation + retry
        retry = r.edit
        self.assertIn("не подходит", retry.text)
        self.assertNotIn(ex["options"][wrong], [b.text for b in e.buttons(retry)])
        self.assertIn("🤷 Не знаю", [b.text for b in e.buttons(retry)])
        self.assertNotIn("Правильный ответ", retry.text)               # answer not revealed yet
        # word is already saved with an error, scheduled for tomorrow
        row = e.repo.all_words(UID)[0]
        self.assertEqual((row["incorrect_count"], row["next_review"]), (1, "2026-03-03"))
        wrong2 = next(i for i in range(4) if i not in (ex["answer"], wrong))
        r = e.press(retry, f"o{wrong2}")                              # 2nd wrong: reveal
        self.assertIn("Правильный ответ", r.edit.text)
        self.assertIn("▶️ Продолжить", [b.text for b in e.buttons(r.edit)])
        self.assertEqual(e.repo.all_words(UID)[0]["incorrect_count"], 1)   # counted once

    def test_wrong_then_correct_retry(self):
        e = self.env
        scr = e.last(e.press(e.pick_level().send[0], "go"))
        act, _ = e.act_of(scr)
        ans = act["state"]["ex"]["answer"]
        r = e.press(scr, f"o{(ans + 1) % 4}")
        r = e.press(r.edit, f"o{ans}")
        self.assertIn("Теперь верно", r.edit.text)
        self.assertEqual(sorted(b.text for b in e.buttons(r.edit)), ["▶️ Продолжить", "🔁 Повторить слово"])

    def test_dont_know_button(self):
        e = self.env
        scr = e.last(e.press(e.pick_level().send[0], "go"))
        act, _ = e.act_of(scr)
        r = e.press(scr, "dk")
        self.assertTrue(r.toast and r.edit is None)                   # not allowed before a first try
        r = e.press(scr, f"o{(act['state']['ex']['answer'] + 1) % 4}")
        r = e.press(r.edit, "dk")
        self.assertIn("Правильный ответ", r.edit.text)

    def test_repeat_word_does_not_count_as_new(self):
        e = self.env
        scr = e.last(e.press(e.pick_level().send[0], "go"))
        r = e.answer(scr)
        fb = r.edit
        r2 = e.press(fb, "rp")
        self.assertIn("Повторяем слово", r2.send[0].text)
        self.assertEqual(len(e.repo.all_words(UID)), 1)
        self.assertEqual(e.act_of(fb)[0]["state"]["pos"], 0)
        e.press(fb, "nx")                                             # feedback buttons still work afterwards

    def test_stale_and_double_press(self):
        e = self.env
        card = e.pick_level().send[0]
        e.press(card, "go")
        r = e.press(card, "go")                                       # old card button again
        self.assertTrue(r.toast_alert)
        self.assertEqual(r.send, [])
        # foreign user cannot use someone else's button
        act, rev = e.act_of(card)
        r = e.L.handle_action(999, act["id"], rev, "go")
        self.assertTrue(r.toast_alert)

    def test_progress_survives_restart_and_mid_lesson_resume(self):
        e = self.env
        card = e.pick_level().send[0]
        r = e.finish_new_word(card)                                   # first word done, second card shown
        second_card = r.send[0]
        self.assertIn("Новое слово 2/2", second_card.text)
        learned = e.repo.learned_ids(UID)
        e.db.close()
        e2 = Env(self.path, clock=e.clock, seed=99)                   # "bot restarted"
        self.assertEqual(e2.repo.learned_ids(UID), learned)
        resumed = e2.L.start_daily(UID).send[0]
        self.assertIn("Новое слово 2/2", resumed.text)                # exactly where we stopped
        done = e2.press(resumed, "go")
        self.assertTrue(e2.buttons(done.send[0]))

    def test_review_scheduling_across_days(self):
        e = self.env
        e.pick_level()
        e.play_daily()
        first_ids = e.repo.learned_ids(UID)
        e.clock.advance(1)
        self.assertEqual(e.repo.count_due(UID, e.clock.today()), 2)
        done = e.play_daily()                                         # day 2: 2 new + 2 due reviews
        self.assertEqual(done.text, texts.DAILY_DONE)
        rows = {r["word_id"]: r for r in e.repo.all_words(UID)}
        self.assertEqual(len(rows), 4)
        for wid in first_ids:
            self.assertEqual(rows[wid]["stage"], 1)
            self.assertEqual(rows[wid]["next_review"], "2026-03-06")  # +3 days from 2026-03-03
            self.assertEqual(rows[wid]["reviewed_ok"], 1)
        for wid in set(rows) - first_ids:
            self.assertEqual(rows[wid]["next_review"], "2026-03-04")  # new today -> tomorrow
        # not every old word is repeated every day
        e.clock.advance(1)
        due_ids = {r["word_id"] for r in e.repo.due_words(UID, e.clock.today(), 50)}
        self.assertEqual(due_ids, set(rows) - first_ids)

    def test_review_mistake_gives_retry_and_earlier_review(self):
        e = self.env
        e.pick_level()
        e.play_daily()
        e.clock.advance(1)
        resp = e.L.start_review(UID)
        scr = resp.send[0]
        self.assertIn("Повторение 1/2", scr.text)
        r = e.answer(scr, correct=False)
        scr = e.last(r)
        r = e.answer(scr, correct=False)                              # reveal
        scr = e.last(r)
        wid = e.act_of(scr)[0]["state"]["items"][0]["word"]
        row = e.repo.get_word(UID, wid)
        self.assertEqual((row["stage"], row["next_review"]), (0, "2026-03-04"))
        items = e.act_of(scr)[0]["state"]["items"]
        self.assertEqual(items[-1]["kind"], "retry")                  # second chance queued
        self.assertEqual(items[-1]["word"], wid)

    def test_review_when_nothing_due(self):
        e = self.env
        e.pick_level()
        e.play_daily()
        r = e.L.start_review(UID)
        self.assertEqual(r.send[0].text, texts.REVIEW_NONE)
        e.clock.advance(1)
        self.assertIn("Повторение 1/2", e.L.start_review(UID).send[0].text)

    def test_practice_locked_then_distinct_words(self):
        e = self.env
        e.pick_level()
        self.assertIn("откроются после урока", e.L.start_practice(UID).send[0].text)
        for _ in range(3):                                            # build up a vocabulary: 3 days
            e.play_daily()
            e.clock.advance(1)
        e.play_daily()
        self.assertGreaterEqual(len(e.repo.all_words(UID)), 6)
        for seed in range(8):
            e.L.rng = random.Random(seed)
            resp = e.L.start_practice(UID)
            act, _ = e.act_of(resp.send[0])
            items = act["state"]["items"]
            self.assertEqual(len(items), 4)
            self.assertEqual(len({i["word"] for i in items}), 4)      # a different word each time
            self.assertEqual(len({(i["word"], i["ex_type"]) for i in items}), 4)
            learned = e.repo.learned_ids(UID)
            self.assertTrue({i["word"] for i in items} <= learned)    # only words the user has studied

    def test_practice_small_vocabulary_uses_only_known_words(self):
        e = self.env
        e.pick_level()
        e.play_daily()                                                # only 2 words learned
        resp = e.L.start_practice(UID)
        act, _ = e.act_of(resp.send[0])
        items = act["state"]["items"]
        self.assertEqual(len(items), 4)
        self.assertTrue({i["word"] for i in items} <= e.repo.learned_ids(UID))
        self.assertEqual(len({(i["word"], i["ex_type"]) for i in items}), 4)   # never the same exercise twice

    def test_practice_completes(self):
        e = self.env
        e.pick_level()
        e.play_daily()
        scr = e.L.start_practice(UID).send[0]
        for _ in range(4):
            r = e.answer(scr)
            r = e.press(e.last(r), "nx")
            scr = r.send[0]
        self.assertIn("Тренировка завершена", scr.text)

    def test_reset_requires_confirmation(self):
        e = self.env
        e.pick_level()
        e.play_daily()
        prompt = e.L.reset_prompt()
        self.assertEqual(len(e.repo.all_words(UID)), 2)               # showing the prompt deletes nothing
        e.L.handle_reset(UID, "no")
        self.assertEqual(len(e.repo.all_words(UID)), 2)
        e.L.handle_reset(UID, "yes")
        self.assertEqual(e.repo.all_words(UID), [])
        self.assertEqual(e.repo.get_user(UID)["level"], "A2")         # settings survive
        self.assertEqual([b.data for b in e.buttons(prompt)], ["rst:yes", "rst:no"])

    def test_progress_numbers_and_honesty(self):
        e = self.env
        e.pick_level()
        e.play_daily()
        e.clock.advance(1)
        e.play_daily()
        stats = e.repo.stats(UID, e.clock.today(), e.clock.today() - timedelta(days=6))
        self.assertEqual((stats["total"], stats["week"], stats["reviewed"], stats["known"]), (4, 4, 2, 0))
        scr = e.L.progress_screen(UID)
        self.assertIn("Серия дней: <b>2</b>", scr.text)

    def test_level_change_keeps_history(self):
        e = self.env
        e.pick_level("A2")
        e.play_daily()
        e.L.choose_level(UID, "B2", onboarding=False)
        self.assertEqual(len(e.repo.all_words(UID)), 2)
        e.clock.advance(1)
        e.play_daily()
        levels = {content.get_word(w)["level"] for w in e.repo.learned_ids(UID)}
        self.assertIn("B2", levels)

    def test_new_words_never_repeat_and_vocab_exhaustion(self):
        e = self.env
        e.pick_level("A2")
        for _ in range(len(content.WORDS) // 2 + 2):
            e.play_daily()
            e.clock.advance(1)
        self.assertEqual(len(e.repo.learned_ids(UID)), len(content.WORDS))
        r = e.L.start_daily(UID)                                      # nothing new left, reviews may remain
        self.assertTrue(r.send)

    def test_settings_and_notifications(self):
        e = self.env
        e.pick_level()
        e.L.handle_setting(UID, "target", "3")
        e.L.handle_setting(UID, "time", "19:00")                      # also enables reminders
        s = e.repo.get_settings(UID)
        self.assertEqual((s["daily_target"], s["notify_enabled"], s["notify_time"]), (3, 1, "19:00"))
        self.assertEqual(e.L.users_to_remind(), [UID])                # fake now() is 19:30, lesson unfinished
        e.L.mark_reminded(UID)
        self.assertEqual(e.L.users_to_remind(), [])                   # at most once a day
        e.clock.advance(1)
        e.play_daily()
        self.assertEqual(e.L.users_to_remind(), [])                   # lesson finished -> no reminder

    def test_notification_offer_after_first_lesson_only_once(self):
        e = self.env
        e.pick_level()
        done_resp = None
        resp = e.L.start_daily(UID)
        screen = resp.send[0]
        while True:                                                   # play the lesson, keep the last Response
            act, _ = e.act_of(screen)
            if act["state"]["phase"] == "card":
                screen = e.last(e.press(screen, "go"))
                continue
            screen = e.last(e.answer(screen))
            r = e.press(screen, "nx")
            if not any(b.data.startswith("a:") for b in e.buttons(r.send[0])):
                done_resp = r
                break
            screen = r.send[0]
        self.assertEqual(done_resp.send[0].text, texts.DAILY_DONE)
        offer = done_resp.send[1]
        self.assertIn("напоминания", offer.text)
        self.assertIn("set:onb:19:00", [b.data for b in e.buttons(offer)])
        r = e.L.handle_setting(UID, "onb", "19:00")
        s = e.repo.get_settings(UID)
        self.assertEqual((s["notify_enabled"], s["notify_time"]), (1, "19:00"))
        self.assertIn("19:00", r.edit.text)
        # next day: not offered again
        e.clock.advance(1)
        done = e.play_daily()
        self.assertEqual(done.text, texts.DAILY_DONE)

    def test_notification_offer_declined_not_repeated(self):
        e = self.env
        e.pick_level()
        e.play_daily()
        self.assertEqual(e.repo.get_settings(UID)["notify_prompted"], 1)
        e.L.handle_setting(UID, "onb", "no")
        self.assertEqual(e.repo.get_settings(UID)["notify_enabled"], 0)

    def test_start_and_home_screens_follow_state(self):
        e = self.env
        self.assertEqual(e.L.start_screen(UID).text, texts.WELCOME)
        e.pick_level()
        self.assertEqual(e.buttons(e.L.home_screen(UID))[0].text, "▶️ Продолжить")
        e.play_daily()
        self.assertEqual(e.buttons(e.L.home_screen(UID))[0].text, "🧠 Дополнительные задания")
        e.clock.advance(1)
        self.assertEqual(e.buttons(e.L.home_screen(UID))[0].text, "▶️ Начать")
        self.assertTrue(any("Повторение" in b.text for b in e.buttons(e.L.home_screen(UID))))


class DatabaseTests(unittest.TestCase):
    def test_migrations_idempotent_and_keep_data(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "k.db")
            e = Env(path)
            e.pick_level()
            e.db.close()
            db = Database(path)
            db.migrate()
            self.assertEqual(db.schema_version(), 2)
            self.assertEqual(Repository(db).get_user(UID)["level"], "A2")


if __name__ == "__main__":
    unittest.main()
