"""Vocabulary access layer: validation + lookup helpers."""
from __future__ import annotations

from content.words import WORDS

LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]
POS_RU = {"verb": "глагол", "noun": "существительное", "adjective": "прилагательное", "adverb": "наречие"}

_REQUIRED = (
    "id", "word", "level", "pos", "definition", "ru", "ru_short", "explanation", "example",
    "example_ru", "mnemonics", "best", "distractors_ru", "distractors_en", "cloze", "context",
)


def validate(words: list[dict]) -> None:
    """Fail fast on a broken vocabulary entry (called at import and in tests)."""
    seen: set[str] = set()
    for w in words:
        missing = [k for k in _REQUIRED if k not in w]
        if missing:
            raise ValueError(f"word {w.get('id')!r}: missing fields {missing}")
        if w["id"] in seen:
            raise ValueError(f"duplicate word id {w['id']!r}")
        seen.add(w["id"])
        if w["level"] not in LEVELS:
            raise ValueError(f"{w['id']}: bad level {w['level']}")
        if not 2 <= len(w["mnemonics"]) <= 3 or not 0 <= w["best"] < len(w["mnemonics"]):
            raise ValueError(f"{w['id']}: need 2-3 mnemonics and a valid `best`")
        if len(w["distractors_ru"]) < 3 or len(w["distractors_en"]) < 3:
            raise ValueError(f"{w['id']}: need >= 3 distractors of each kind")
        if "___" not in w["cloze"]:
            raise ValueError(f"{w['id']}: cloze needs a ___ blank")
        if w["word"] in w["distractors_en"] or w["ru_short"] in w["distractors_ru"]:
            raise ValueError(f"{w['id']}: a distractor equals the correct answer")


validate(WORDS)
BY_ID: dict[str, dict] = {w["id"]: w for w in WORDS}


def get_word(word_id: str) -> dict | None:
    return BY_ID.get(word_id)


def levels_available(track: str = "general") -> list[str]:
    present = {w["level"] for w in WORDS if w.get("track", "general") == track}
    return [lv for lv in LEVELS if lv in present]
