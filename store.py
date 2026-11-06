"""Profile-isolated SQLite state; callers must explicitly supply the database path."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from typing import Callable


class StaleRevision(RuntimeError):
    """A mutation or solve was based on an obsolete route revision."""


def initial_state() -> dict:
    return {"revision": 0, "origin": None, "return_origin": None, "endpoint": None, "round_trip": False,
            "stops": [], "visited": [], "next_id": None, "precedence": [], "solution": None}


def decode_state(encoded: str) -> dict:
    state = json.loads(encoded)
    # Legacy states did not record the initial start. Use the only known origin,
    # never infer home from visited stops or geometry. Persist on the next write.
    if "return_origin" not in state:
        state["return_origin"] = json.loads(json.dumps(state["origin"]))
    return state


class RouteStore:
    def __init__(self, path: str | Path):
        if not str(path) or str(path) == ":memory:":
            raise ValueError("an explicit persistent profile database path is required")
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # New personal-location databases are private; never chmod an existing
        # caller-managed path as a side effect of opening it.
        self.path.touch(mode=0o600, exist_ok=True)
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS personal_route (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, state TEXT NOT NULL)")
            db.execute("INSERT OR IGNORE INTO personal_route VALUES (1,0,?)", (json.dumps(initial_state()),))

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("PRAGMA busy_timeout=5000")
        try:
            with db:
                yield db
        finally:
            db.close()

    def read(self) -> dict:
        with self._connect() as db:
            return decode_state(db.execute("SELECT state FROM personal_route WHERE id=1").fetchone()[0])

    def mutate(self, edit: Callable[[dict], None], expected_revision: int | None = None) -> dict:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            revision, encoded = db.execute("SELECT revision,state FROM personal_route WHERE id=1").fetchone()
            if expected_revision is not None and revision != expected_revision:
                raise StaleRevision(f"expected revision {expected_revision}, found {revision}")
            state = decode_state(encoded)
            edit(state)
            state["revision"] = revision + 1
            state["solution"] = None
            db.execute("UPDATE personal_route SET revision=?,state=? WHERE id=1", (state["revision"], json.dumps(state, allow_nan=False)))
        return state

    def publish(self, revision: int, solution: dict) -> dict:
        # The check and write share one SQLite write transaction, across processes.
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            actual, encoded = db.execute("SELECT revision,state FROM personal_route WHERE id=1").fetchone()
            if revision != actual:
                raise StaleRevision(f"solve revision {revision} is stale; current revision is {actual}")
            state = decode_state(encoded)
            state["solution"] = solution
            db.execute("UPDATE personal_route SET state=? WHERE id=1 AND revision=?", (json.dumps(state, allow_nan=False), revision))
        return state
