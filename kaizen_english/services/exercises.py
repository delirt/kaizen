"""Exercise generation. Every exercise is about ONE studied word; answer positions are shuffled."""
from __future__ import annotations

import random
from html import escape as e
from typing import Any

EX_TYPES = ("translation", "ru_en", "cloze", "context")


def build_exercise(word: dict, ex_type: str, rng: random.Random) -> dict[str, Any]:
    """Return {'type','prompt','options','answer'(index)} — JSON-serialisable (stored in the DB)."""
    if ex_type == "translation":
        correct, pool = word["ru_short"], word["distractors_ru"]
        prompt = f"🔤 Что означает слово <b>{e(word['word'])}</b>?"
    elif ex_type == "ru_en":
        correct, pool = word["word"], word["distractors_en"]
        prompt = f"🇷🇺➡️🇬🇧 Как будет по-английски «<b>{e(word['ru_short'])}</b>»?"
    elif ex_type == "cloze":
        correct, pool = word["word"], word["distractors_en"]
        prompt = f"✏️ Вставь подходящее слово:\n\n<i>{e(word['cloze'])}</i>"
    elif ex_type == "context":
        correct, pool = word["word"], word["distractors_en"]
        prompt = f"🎯 Какое слово подходит к ситуации?\n\n<i>{e(word['context'])}</i>"
    else:
        raise ValueError(f"unknown exercise type {ex_type!r}")

    options = [correct] + [d for d in dict.fromkeys(pool) if d != correct][:3]
    rng.shuffle(options)
    return {"type": ex_type, "prompt": prompt, "options": options, "answer": options.index(correct)}


def wrong_answer_text(word: dict, ex_type: str, chosen: str) -> str:
    """Short explanation after a wrong answer. It hints but does not give the answer away."""
    head = f"❌ «{e(chosen)}» — не подходит."
    if ex_type == "translation":
        hint = e(word["explanation"])
    elif ex_type == "ru_en":
        hint = f"Подсказка: нужно слово со значением <i>{e(word['definition'])}</i>"
    else:
        hint = f"Подсказка: пропущенное слово означает «{e(word['ru_short'])}»."
    return f"{head}\n{hint}\n\nПопробуй ещё раз 👇"


def choose_type(rng: random.Random, last_type: str | None, used: list[str]) -> str:
    """Pick an exercise type: not the one this word had last time, preferably not yet used in this run."""
    pool = [t for t in EX_TYPES if t != last_type] or list(EX_TYPES)
    fresh = [t for t in pool if t not in used] or pool
    return rng.choice(fresh)
