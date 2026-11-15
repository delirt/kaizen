"""Spaced-repetition rules (pure functions, no I/O)."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

# Days until the next review, indexed by the CURRENT stage:
# learned -> +1d, 1st review ok -> +3d, 2nd -> +7d, 3rd -> +14d, then gradually longer.
INTERVALS_DAYS = (1, 3, 7, 14, 30, 60, 120)
REVIEWING_STAGE = 3   # >= 3 successful reviews -> "reviewing"
KNOWN_STAGE = 5       # >= 5 successful reviews (gap >= 30d) -> "known"


def interval_for_stage(stage: int) -> int:
    return INTERVALS_DAYS[min(max(stage, 0), len(INTERVALS_DAYS) - 1)]


def status_for_stage(stage: int) -> str:
    if stage >= KNOWN_STAGE:
        return "known"
    return "reviewing" if stage >= REVIEWING_STAGE else "learning"


def new_word_record(today: date, correct: bool, ex_type: str | None) -> dict[str, Any]:
    """Row for a word the user has just studied (stage 0, first review tomorrow)."""
    return dict(
        status="learning",
        first_learned=today.isoformat(),
        last_review=None,
        next_review=(today + timedelta(days=interval_for_stage(0))).isoformat(),
        correct_count=1 if correct else 0,
        incorrect_count=0 if correct else 1,
        stage=0,
        reviewed_ok=0,
        last_ex_type=ex_type,
    )


def apply_answer(rec: dict[str, Any], correct: bool, today: date, ex_type: str | None = None) -> dict[str, Any]:
    """Return the updated fields after one answered exercise for an already-learned word.

    * due word + correct   -> stage+1, next review after the longer interval
    * due word + wrong     -> stage-1 (min 0), review again tomorrow
    * not due + correct    -> only the counter grows (no free interval boost from extra practice)
    * not due + wrong      -> stage-1, review pulled earlier (at most tomorrow)
    """
    due = date.fromisoformat(rec["next_review"]) <= today
    tomorrow = today + timedelta(days=1)
    out = dict(rec)
    out["last_review"] = today.isoformat()
    if ex_type:
        out["last_ex_type"] = ex_type
    if correct:
        out["correct_count"] = rec["correct_count"] + 1
        if due:
            out["stage"] = rec["stage"] + 1
            out["reviewed_ok"] = rec["reviewed_ok"] + 1
            out["next_review"] = (today + timedelta(days=interval_for_stage(out["stage"]))).isoformat()
    else:
        out["incorrect_count"] = rec["incorrect_count"] + 1
        out["stage"] = max(0, rec["stage"] - 1)
        current = date.fromisoformat(rec["next_review"])
        out["next_review"] = (tomorrow if due else min(current, tomorrow)).isoformat()
    out["status"] = status_for_stage(out["stage"])
    return out


def streak(completed_days: list[date], today: date) -> int:
    """Consecutive days with a finished daily lesson. Still alive if yesterday was the last one."""
    days = set(completed_days)
    cursor = today if today in days else today - timedelta(days=1)
    count = 0
    while cursor in days:
        count += 1
        cursor -= timedelta(days=1)
    return count
