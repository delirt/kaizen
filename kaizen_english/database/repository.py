"""All SQL lives here. Services never write SQL themselves."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any

from database.db import Database

SETTINGS_FIELDS = {"daily_target", "notify_enabled", "notify_time", "last_notified_date", "mode",
                   "notify_prompted"}
WORD_FIELDS = (
    "status", "first_learned", "last_review", "next_review", "correct_count",
    "incorrect_count", "stage", "reviewed_ok", "last_ex_type",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Repository:
    def __init__(self, db: Database):
        self.db = db

    # ---- users & settings -------------------------------------------------
    def ensure_user(self, user_id: int, username: str | None, first_name: str | None) -> dict[str, Any]:
        with self.db.transaction():
            row = self.get_user(user_id)
            if row is None:
                self.db.execute(
                    "INSERT INTO users(user_id, username, first_name, registered_at) VALUES (?,?,?,?)",
                    (user_id, username, first_name, _now()),
                )
                self.db.execute("INSERT INTO user_settings(user_id) VALUES (?)", (user_id,))
            elif (row["username"], row["first_name"]) != (username, first_name):
                self.db.execute(
                    "UPDATE users SET username=?, first_name=? WHERE user_id=?",
                    (username, first_name, user_id),
                )
            return self.get_user(user_id)  # type: ignore[return-value]

    def get_user(self, user_id: int) -> dict[str, Any] | None:
        return self.db.fetchone("SELECT * FROM users WHERE user_id=?", (user_id,))

    def update_user(self, user_id: int, **fields: Any) -> None:
        allowed = {"level", "state", "last_learning_date"}
        if not fields or not set(fields) <= allowed:
            raise ValueError(f"bad user fields: {fields}")
        cols = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE users SET {cols} WHERE user_id=?", (*fields.values(), user_id))

    def get_settings(self, user_id: int) -> dict[str, Any]:
        return self.db.fetchone("SELECT * FROM user_settings WHERE user_id=?", (user_id,)) or {}

    def update_settings(self, user_id: int, **fields: Any) -> None:
        if not fields or not set(fields) <= SETTINGS_FIELDS:
            raise ValueError(f"bad settings fields: {fields}")
        cols = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE user_settings SET {cols} WHERE user_id=?", (*fields.values(), user_id))

    def notification_candidates(self, today: str) -> list[dict[str, Any]]:
        return self.db.fetchall(
            """SELECT u.user_id, s.notify_time, s.daily_target
               FROM users u JOIN user_settings s ON s.user_id = u.user_id
               WHERE s.notify_enabled = 1
                 AND (s.last_notified_date IS NULL OR s.last_notified_date <> ?)""",
            (today,),
        )

    # ---- vocabulary progress ---------------------------------------------
    def get_word(self, user_id: int, word_id: str) -> dict[str, Any] | None:
        return self.db.fetchone(
            "SELECT * FROM user_words WHERE user_id=? AND word_id=?", (user_id, word_id)
        )

    def all_words(self, user_id: int) -> list[dict[str, Any]]:
        return self.db.fetchall("SELECT * FROM user_words WHERE user_id=?", (user_id,))

    def learned_ids(self, user_id: int) -> set[str]:
        return {r["word_id"] for r in self.db.fetchall(
            "SELECT word_id FROM user_words WHERE user_id=?", (user_id,))}

    def insert_word(self, user_id: int, word_id: str, **fields: Any) -> None:
        cols = ["user_id", "word_id", *WORD_FIELDS]
        values = [user_id, word_id, *[fields.get(f) for f in WORD_FIELDS]]
        self.db.execute(
            f"INSERT OR IGNORE INTO user_words({', '.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            values,
        )

    def update_word(self, user_id: int, word_id: str, **fields: Any) -> None:
        if not set(fields) <= set(WORD_FIELDS):
            raise ValueError(f"bad word fields: {fields}")
        cols = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(
            f"UPDATE user_words SET {cols} WHERE user_id=? AND word_id=?",
            (*fields.values(), user_id, word_id),
        )

    def due_words(self, user_id: int, today: date, limit: int) -> list[dict[str, Any]]:
        return self.db.fetchall(
            """SELECT * FROM user_words WHERE user_id=? AND next_review <= ?
               ORDER BY next_review ASC, stage ASC, word_id ASC LIMIT ?""",
            (user_id, today.isoformat(), limit),
        )

    def count_due(self, user_id: int, today: date) -> int:
        return self.db.scalar(
            "SELECT COUNT(*) FROM user_words WHERE user_id=? AND next_review <= ?",
            (user_id, today.isoformat()),
        )

    def stats(self, user_id: int, today: date, week_start: date) -> dict[str, int]:
        row = self.db.fetchone(
            """SELECT COUNT(*) AS total,
                      COALESCE(SUM(first_learned >= ?), 0) AS week,
                      COALESCE(SUM(reviewed_ok > 0), 0) AS reviewed,
                      COALESCE(SUM(next_review <= ?), 0) AS due,
                      COALESCE(SUM(status = 'known'), 0) AS known
               FROM user_words WHERE user_id=?""",
            (week_start.isoformat(), today.isoformat(), user_id),
        )
        return {k: int(v) for k, v in (row or {}).items()}

    # ---- activities -------------------------------------------------------
    @staticmethod
    def _activity(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if row is not None:
            row["state"] = json.loads(row["state"])
        return row

    def create_activity(self, user_id: int, kind: str, day: date, state: dict, status: str = "active") -> int:
        cur = self.db.execute(
            "INSERT INTO activities(user_id, kind, day, status, state, created_at, finished_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (user_id, kind, day.isoformat(), status, json.dumps(state, ensure_ascii=False), _now(),
             _now() if status == "done" else None),
        )
        return int(cur.lastrowid)

    def get_activity(self, activity_id: int) -> dict[str, Any] | None:
        return self._activity(self.db.fetchone("SELECT * FROM activities WHERE id=?", (activity_id,)))

    def get_daily(self, user_id: int, day: date) -> dict[str, Any] | None:
        return self._activity(self.db.fetchone(
            "SELECT * FROM activities WHERE user_id=? AND kind='daily' AND day=?",
            (user_id, day.isoformat())))

    def save_activity(self, activity_id: int, state: dict, rev: int, status: str = "active") -> None:
        self.db.execute(
            "UPDATE activities SET state=?, rev=?, status=?, finished_at=? WHERE id=?",
            (json.dumps(state, ensure_ascii=False), rev, status,
             _now() if status == "done" else None, activity_id),
        )

    def abandon_active(self, user_id: int, kind: str, before_day: date | None = None) -> None:
        sql = "UPDATE activities SET status='abandoned' WHERE user_id=? AND kind=? AND status='active'"
        params: list[Any] = [user_id, kind]
        if before_day is not None:
            sql += " AND day < ?"
            params.append(before_day.isoformat())
        self.db.execute(sql, params)

    def completed_days(self, user_id: int) -> list[date]:
        rows = self.db.fetchall(
            "SELECT DISTINCT day FROM activities WHERE user_id=? AND kind='daily' AND status='done'"
            " ORDER BY day DESC", (user_id,))
        return [date.fromisoformat(r["day"]) for r in rows]

    # ---- reset ------------------------------------------------------------
    def reset_progress(self, user_id: int) -> None:
        """Wipe learning history of ONE user (called only after explicit confirmation)."""
        with self.db.transaction():
            self.db.execute("DELETE FROM user_words WHERE user_id=?", (user_id,))
            self.db.execute("DELETE FROM activities WHERE user_id=?", (user_id,))
            self.db.execute("UPDATE users SET state='idle', last_learning_date=NULL WHERE user_id=?", (user_id,))
