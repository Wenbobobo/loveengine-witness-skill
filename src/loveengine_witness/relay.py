"""SQLite-backed at-least-once Relay Hub storage."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RelayMessage:
    recipient: str
    task_id: str
    payload: str
    attempts: int


class RelayStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                recipient TEXT NOT NULL,
                task_id TEXT NOT NULL,
                issuer TEXT,
                nonce TEXT,
                payload TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                accepted INTEGER NOT NULL DEFAULT 0,
                acked INTEGER NOT NULL DEFAULT 0,
                receipt TEXT,
                PRIMARY KEY (recipient, task_id),
                UNIQUE (recipient, issuer, nonce)
            )
            """
        )
        columns = {
            str(row[1])
            for row in self.connection.execute("PRAGMA table_info(messages)")
        }
        if "accepted" not in columns:
            self.connection.execute(
                "ALTER TABLE messages ADD COLUMN accepted INTEGER NOT NULL DEFAULT 0"
            )
        self.connection.commit()

    def enqueue(
        self,
        recipient: str,
        task_id: str,
        payload: str,
        issuer: str = "",
        nonce: str = "",
    ) -> bool:
        try:
            self.connection.execute(
                """
                INSERT INTO messages(recipient, task_id, issuer, nonce, payload)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    recipient,
                    task_id,
                    issuer or None,
                    nonce or None,
                    payload,
                ),
            )
            self.connection.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def pending(
        self,
        recipient: str,
        exclude_task_ids: set[str] | None = None,
    ) -> list[RelayMessage]:
        rows = self.connection.execute(
            """
            SELECT task_id, payload, attempts
            FROM messages
            WHERE recipient = ? AND acked = 0
            ORDER BY rowid
            """,
            (recipient,),
        ).fetchall()
        result = []
        for task_id, payload, attempts in rows:
            if exclude_task_ids and task_id in exclude_task_ids:
                continue
            attempts += 1
            self.connection.execute(
                """
                UPDATE messages SET attempts = ?
                WHERE recipient = ? AND task_id = ?
                """,
                (attempts, recipient, task_id),
            )
            result.append(RelayMessage(recipient, task_id, payload, attempts))
        self.connection.commit()
        return result

    def ack(self, recipient: str, task_id: str, receipt: str) -> bool:
        cursor = self.connection.execute(
            """
            UPDATE messages SET acked = 1, receipt = ?
            WHERE recipient = ? AND task_id = ?
              AND accepted = 1 AND acked = 0 AND receipt IS NULL
            """,
            (receipt, recipient, task_id),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def accept(self, recipient: str, task_id: str) -> bool:
        cursor = self.connection.execute(
            """
            UPDATE messages SET accepted = 1
            WHERE recipient = ? AND task_id = ? AND accepted = 0
            """,
            (recipient, task_id),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def receipt(self, recipient: str, task_id: str) -> str | None:
        row = self.connection.execute(
            """
            SELECT receipt FROM messages
            WHERE recipient = ? AND task_id = ?
            """,
            (recipient, task_id),
        ).fetchone()
        return None if row is None else row[0]

    def task_state(self, recipient: str, task_id: str) -> dict[str, object] | None:
        row = self.connection.execute(
            """
            SELECT payload, accepted, acked, receipt
            FROM messages
            WHERE recipient = ? AND task_id = ?
            """,
            (recipient, task_id),
        ).fetchone()
        if row is None:
            return None
        return {
            "payload": str(row[0]),
            "accepted": bool(row[1]),
            "acked": bool(row[2]),
            "receipt": row[3],
        }

    def metrics(self) -> dict[str, int]:
        queued, delivered, accepted, acked = self.connection.execute(
            """
            SELECT COUNT(*), COALESCE(SUM(attempts), 0),
                   COALESCE(SUM(accepted), 0), COALESCE(SUM(acked), 0)
            FROM messages
            """
        ).fetchone()
        return {
            "accepted": int(accepted),
            "acked": int(acked),
            "delivered": int(delivered),
            "queued": int(queued),
        }
