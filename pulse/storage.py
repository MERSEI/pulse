"""Состояние таргетов между прогонами и перезапусками.

SQLite, а не Postgres: данных здесь на десятки строк, а watchdog должен
подниматься одной командой и переживать перезапуск контейнера. На Railway
файл кладётся на volume.

Без персистентности перезапуск сервиса обнулял бы счётчики и повторно
рассылал алерты по уже известным инцидентам — ровно тот шум, от которого
защищает AlertPolicy.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from .models import Health, TargetState

SCHEMA = """
CREATE TABLE IF NOT EXISTS target_state (
    target            TEXT PRIMARY KEY,
    health            TEXT NOT NULL,
    consecutive_fails INTEGER NOT NULL DEFAULT 0,
    consecutive_oks   INTEGER NOT NULL DEFAULT 0,
    alerted           INTEGER NOT NULL DEFAULT 0,
    last_alert_at     TEXT,
    muted_until       TEXT
);

CREATE TABLE IF NOT EXISTS incident (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    target     TEXT NOT NULL,
    opened_at  TEXT NOT NULL,
    closed_at  TEXT,
    summary    TEXT NOT NULL,
    triage     TEXT
);

CREATE INDEX IF NOT EXISTS incident_target_idx ON incident(target, opened_at DESC);
"""


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class Store:
    def __init__(self, path: Path | str):
        self._path = str(path)
        parent = Path(self._path).parent
        if parent and str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def load(self, target: str) -> TargetState:
        """Достать состояние таргета; для незнакомого вернуть чистое."""
        with self._connect() as conn, closing(conn.cursor()) as cur:
            cur.execute("SELECT * FROM target_state WHERE target = ?", (target,))
            row = cur.fetchone()

        if row is None:
            return TargetState(target=target)

        return TargetState(
            target=row["target"],
            health=Health(row["health"]),
            consecutive_fails=row["consecutive_fails"],
            consecutive_oks=row["consecutive_oks"],
            alerted=bool(row["alerted"]),
            last_alert_at=_dt(row["last_alert_at"]),
            muted_until=_dt(row["muted_until"]),
        )

    def save(self, state: TargetState) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO target_state
                    (target, health, consecutive_fails, consecutive_oks,
                     alerted, last_alert_at, muted_until)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(target) DO UPDATE SET
                    health            = excluded.health,
                    consecutive_fails = excluded.consecutive_fails,
                    consecutive_oks   = excluded.consecutive_oks,
                    alerted           = excluded.alerted,
                    last_alert_at     = excluded.last_alert_at,
                    muted_until       = excluded.muted_until
                """,
                (
                    state.target,
                    state.health.value,
                    state.consecutive_fails,
                    state.consecutive_oks,
                    int(state.alerted),
                    state.last_alert_at.isoformat() if state.last_alert_at else None,
                    state.muted_until.isoformat() if state.muted_until else None,
                ),
            )

    def all_states(self) -> list[TargetState]:
        with self._connect() as conn, closing(conn.cursor()) as cur:
            cur.execute("SELECT target FROM target_state ORDER BY target")
            targets = [row["target"] for row in cur.fetchall()]
        return [self.load(t) for t in targets]

    def open_incident(self, target: str, opened_at: datetime, summary: str, triage: str | None) -> int:
        with self._connect() as conn, closing(conn.cursor()) as cur:
            cur.execute(
                "INSERT INTO incident (target, opened_at, summary, triage) VALUES (?, ?, ?, ?)",
                (target, opened_at.isoformat(), summary, triage),
            )
            return int(cur.lastrowid)

    def close_incident(self, target: str, closed_at: datetime) -> None:
        """Закрыть последний открытый инцидент таргета."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE incident SET closed_at = ?
                WHERE id = (
                    SELECT id FROM incident
                    WHERE target = ? AND closed_at IS NULL
                    ORDER BY opened_at DESC LIMIT 1
                )
                """,
                (closed_at.isoformat(), target),
            )

    def recent_incidents(self, limit: int = 10) -> list[sqlite3.Row]:
        with self._connect() as conn, closing(conn.cursor()) as cur:
            cur.execute(
                "SELECT * FROM incident ORDER BY opened_at DESC LIMIT ?", (limit,)
            )
            return cur.fetchall()
