"""Content-addressed artifacts and SQLite metadata for M4."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Protocol

from .canonical import canonical_json_bytes
from .errors import LoveEngineError
from .hashes import sha256_prefixed
from .live_protocol import verify_live_event


class ArtifactStore(Protocol):
    def put(self, data: bytes) -> str: ...
    def get(self, digest: str) -> bytes: ...
    def exists(self, digest: str) -> bool: ...


class LocalArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def path_for(self, digest: str) -> Path:
        if not digest.startswith("sha256:") or len(digest) != 71:
            raise LoveEngineError("invalid_artifact_hash", digest)
        raw = digest.removeprefix("sha256:")
        return self.root / "sha256" / raw[:2] / raw

    def put(self, data: bytes) -> str:
        digest = sha256_prefixed(data)
        path = self.path_for(digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_bytes() != data:
            raise LoveEngineError("artifact_conflict", digest)
        if not path.exists():
            path.write_bytes(data)
        return digest

    def get(self, digest: str) -> bytes:
        path = self.path_for(digest)
        if not path.exists():
            raise LoveEngineError("artifact_missing", digest)
        data = path.read_bytes()
        if sha256_prefixed(data) != digest:
            raise LoveEngineError("artifact_hash_mismatch", digest)
        return data

    def exists(self, digest: str) -> bool:
        try:
            self.get(digest)
        except LoveEngineError:
            return False
        return True


class LiveMetadataStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_hash TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    UNIQUE(session_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS bundles (
                    session_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    bundle_hash TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    PRIMARY KEY(session_id, revision)
                );
                CREATE TABLE IF NOT EXISTS disputes (
                    dispute_id TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reviews (
                    review_id TEXT PRIMARY KEY,
                    dispute_id TEXT NOT NULL,
                    node TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    UNIQUE(dispute_id, node)
                );
                CREATE TABLE IF NOT EXISTS cursors (
                    consumer TEXT PRIMARY KEY,
                    cursor TEXT NOT NULL
                );
                """
            )

    @staticmethod
    def _dump(value: dict[str, Any]) -> str:
        return canonical_json_bytes(value).decode("utf-8")

    def create_session(self, value: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as db:
            try:
                db.execute(
                    "INSERT INTO sessions(session_id, value_json) VALUES (?, ?)",
                    (value["session_id"], self._dump(value)),
                )
            except sqlite3.IntegrityError as exc:
                existing = self.get_session(value["session_id"])
                if canonical_json_bytes(existing) == canonical_json_bytes(value):
                    return existing
                raise LoveEngineError(
                    "session_conflict", "session ID already has different content"
                ) from exc
        return value

    def get_session(self, session_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute(
                "SELECT value_json FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        if row is None:
            raise LoveEngineError("session_not_found", session_id)
        return json.loads(row["value_json"])

    def list_sessions(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT value_json FROM sessions ORDER BY session_id"
            ).fetchall()
        return [json.loads(row["value_json"]) for row in rows]

    def append_event(self, event: dict[str, Any]) -> dict[str, Any]:
        session = self.get_session(event["session_id"])
        if session["status"] == "closed":
            raise LoveEngineError("session_closed", event["session_id"])
        with self.connect() as db:
            existing = db.execute(
                "SELECT value_json FROM events WHERE event_id = ?",
                (event["event_id"],),
            ).fetchone()
            if existing is not None:
                if canonical_json_bytes(json.loads(existing["value_json"])) == canonical_json_bytes(
                    event
                ):
                    return {"duplicate": True, "event": event}
                raise LoveEngineError(
                    "event_conflict", "event ID already has different content"
                )
            if not verify_live_event(event):
                raise LoveEngineError("event_hash_mismatch", event["event_id"])
            if int(event["sequence"]) != int(session["next_sequence"]):
                raise LoveEngineError("sequence_gap", event["sequence"])
            if event["previous_event_hash"] != session["head_event_hash"]:
                raise LoveEngineError("hash_chain_broken", event["event_id"])
            db.execute(
                """
                INSERT INTO events(event_id, session_id, sequence, event_hash, value_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    event["event_id"],
                    event["session_id"],
                    int(event["sequence"]),
                    event["event_hash"],
                    self._dump(event),
                ),
            )
            session["next_sequence"] = str(int(event["sequence"]) + 1)
            session["head_event_hash"] = event["event_hash"]
            db.execute(
                "UPDATE sessions SET value_json = ? WHERE session_id = ?",
                (self._dump(session), event["session_id"]),
            )
        return {"duplicate": False, "event": event}

    def list_events(
        self, session_id: str, after_sequence: int = 0
    ) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT value_json FROM events
                WHERE session_id = ? AND sequence > ?
                ORDER BY sequence
                """,
                (session_id, after_sequence),
            ).fetchall()
        return [json.loads(row["value_json"]) for row in rows]

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT value_json FROM events WHERE event_id = ?", (event_id,)
            ).fetchone()
        return None if row is None else json.loads(row["value_json"])

    def close_session(self, session_id: str, closed_at: str) -> dict[str, Any]:
        session = self.get_session(session_id)
        if session["status"] == "closed":
            return session
        session["status"] = "closed"
        session["closed_at"] = str(closed_at)
        with self.connect() as db:
            db.execute(
                "UPDATE sessions SET value_json = ? WHERE session_id = ?",
                (self._dump(session), session_id),
            )
        return session

    def save_bundle(self, bundle: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as db:
            existing = db.execute(
                "SELECT value_json FROM bundles WHERE session_id = ? AND revision = ?",
                (bundle["session_id"], int(bundle["revision"])),
            ).fetchone()
            if existing is not None:
                current = json.loads(existing["value_json"])
                if current["bundle_hash"] == bundle["bundle_hash"]:
                    return current
                raise LoveEngineError("bundle_revision_conflict", bundle["revision"])
            db.execute(
                """
                INSERT INTO bundles(session_id, revision, bundle_hash, value_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    bundle["session_id"],
                    int(bundle["revision"]),
                    bundle["bundle_hash"],
                    self._dump(bundle),
                ),
            )
        return bundle

    def get_bundle(self, session_id: str, revision: str | None = None) -> dict[str, Any]:
        with self.connect() as db:
            if revision is None:
                row = db.execute(
                    """
                    SELECT value_json FROM bundles
                    WHERE session_id = ? ORDER BY revision DESC LIMIT 1
                    """,
                    (session_id,),
                ).fetchone()
            else:
                row = db.execute(
                    "SELECT value_json FROM bundles WHERE session_id = ? AND revision = ?",
                    (session_id, int(revision)),
                ).fetchone()
        if row is None:
            raise LoveEngineError("bundle_not_found", session_id)
        return json.loads(row["value_json"])

    def save_dispute(self, dispute: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as db:
            existing = db.execute(
                "SELECT value_json FROM disputes WHERE dispute_id = ?",
                (dispute["dispute_id"],),
            ).fetchone()
            if existing is not None:
                current = json.loads(existing["value_json"])
                if canonical_json_bytes(current) == canonical_json_bytes(dispute):
                    return current
                raise LoveEngineError("dispute_conflict", dispute["dispute_id"])
            db.execute(
                "INSERT INTO disputes(dispute_id, value_json) VALUES (?, ?)",
                (dispute["dispute_id"], self._dump(dispute)),
            )
        return dispute

    def get_dispute(self, dispute_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute(
                "SELECT value_json FROM disputes WHERE dispute_id = ?", (dispute_id,)
            ).fetchone()
        if row is None:
            raise LoveEngineError("dispute_not_found", dispute_id)
        return json.loads(row["value_json"])

    def save_review(self, review: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as db:
            try:
                db.execute(
                    """
                    INSERT INTO reviews(review_id, dispute_id, node, value_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        review["review_id"],
                        review["dispute_id"],
                        review["node"],
                        self._dump(review),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise LoveEngineError(
                    "duplicate_review_node", review["node"]
                ) from exc
        return review

    def list_reviews(self, dispute_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT value_json FROM reviews WHERE dispute_id = ? ORDER BY review_id",
                (dispute_id,),
            ).fetchall()
        return [json.loads(row["value_json"]) for row in rows]

    def set_cursor(self, consumer: str, cursor: str) -> None:
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO cursors(consumer, cursor) VALUES (?, ?)
                ON CONFLICT(consumer) DO UPDATE SET cursor = excluded.cursor
                """,
                (consumer, str(cursor)),
            )

    def get_cursor(self, consumer: str) -> str | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT cursor FROM cursors WHERE consumer = ?", (consumer,)
            ).fetchone()
        return None if row is None else str(row["cursor"])
