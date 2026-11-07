"""SQLite persistence.

Every pipeline step stores its output, so a failed step can be retried without
re-running (or paying for) the steps before it. Lesson content is append-only:
every AI generation, instructor save and applied QC fix creates a new version,
so nothing an instructor wrote is ever overwritten.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager

from .config import get_settings

STEPS = ["plan", "research", "generate", "qc_research", "qc_instruction"]
_write_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS lessons (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    objective TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS steps (
    lesson_id TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    started_at TEXT,
    finished_at TEXT,
    output TEXT,
    PRIMARY KEY (lesson_id, name)
);
CREATE TABLE IF NOT EXISTS versions (
    lesson_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    author TEXT NOT NULL,
    note TEXT,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (lesson_id, version)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lesson_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL
);
"""


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


@contextmanager
def conn():
    path = get_settings().db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path, timeout=30)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init() -> None:
    with conn() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        # A crash mid-step leaves 'running' rows behind; mark them failed so they can be retried.
        c.execute(
            "UPDATE steps SET status='failed', error='Interrupted (server restarted). Click Retry.' WHERE status='running'"
        )


# ------------------------------------------------------------------ lessons
def create_lesson(title: str, objective: str) -> str:
    lid = uuid.uuid4().hex[:10]
    t = now()
    with _write_lock, conn() as c:
        c.execute("INSERT INTO lessons VALUES (?,?,?,?,?)", (lid, title, objective, t, t))
        for s in STEPS:
            c.execute("INSERT INTO steps (lesson_id, name) VALUES (?,?)", (lid, s))
    return lid


def get_lesson(lid: str) -> dict | None:
    with conn() as c:
        r = c.execute("SELECT * FROM lessons WHERE id=?", (lid,)).fetchone()
    return dict(r) if r else None


def list_lessons() -> list[dict]:
    with conn() as c:
        rows = c.execute(
            """SELECT l.*, (SELECT MAX(version) FROM versions v WHERE v.lesson_id=l.id) AS latest_version
               FROM lessons l ORDER BY l.updated_at DESC"""
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            st = c.execute("SELECT name, status FROM steps WHERE lesson_id=?", (d["id"],)).fetchall()
            d["steps"] = {s["name"]: s["status"] for s in st}
            out.append(d)
    return out


def delete_lesson(lid: str) -> None:
    with _write_lock, conn() as c:
        for t in ("lessons", "steps", "versions", "events"):
            col = "id" if t == "lessons" else "lesson_id"
            c.execute(f"DELETE FROM {t} WHERE {col}=?", (lid,))


def touch(lid: str) -> None:
    with _write_lock, conn() as c:
        c.execute("UPDATE lessons SET updated_at=? WHERE id=?", (now(), lid))


# -------------------------------------------------------------------- steps
def get_steps(lid: str, with_output: bool = True) -> dict[str, dict]:
    with conn() as c:
        rows = c.execute("SELECT * FROM steps WHERE lesson_id=?", (lid,)).fetchall()
    out = {}
    for r in rows:
        d = dict(r)
        d["output"] = json.loads(d["output"]) if (with_output and d["output"]) else None
        out[d["name"]] = d
    return {s: out[s] for s in STEPS if s in out}


def get_step_output(lid: str, name: str):
    with conn() as c:
        r = c.execute("SELECT output FROM steps WHERE lesson_id=? AND name=?", (lid, name)).fetchone()
    return json.loads(r["output"]) if r and r["output"] else None


def set_step(lid: str, name: str, status: str, error: str | None = None, output=None) -> None:
    t = now()
    with _write_lock, conn() as c:
        if status == "running":
            c.execute(
                "UPDATE steps SET status=?, error=NULL, attempts=attempts+1, started_at=?, finished_at=NULL WHERE lesson_id=? AND name=?",
                (status, t, lid, name),
            )
        elif status == "pending":
            c.execute(
                "UPDATE steps SET status='pending', error=NULL, started_at=NULL, finished_at=NULL WHERE lesson_id=? AND name=?",
                (lid, name),
            )
        else:
            c.execute(
                "UPDATE steps SET status=?, error=?, finished_at=?, output=COALESCE(?, output) WHERE lesson_id=? AND name=?",
                (status, error, t, json.dumps(output) if output is not None else None, lid, name),
            )
        c.execute("UPDATE lessons SET updated_at=? WHERE id=?", (t, lid))


def update_step_output(lid: str, name: str, output) -> None:
    with _write_lock, conn() as c:
        c.execute("UPDATE steps SET output=? WHERE lesson_id=? AND name=?", (json.dumps(output), lid, name))


# ----------------------------------------------------------------- versions
def add_version(lid: str, content: dict, author: str, note: str = "") -> int:
    with _write_lock, conn() as c:
        r = c.execute("SELECT COALESCE(MAX(version),0) AS v FROM versions WHERE lesson_id=?", (lid,)).fetchone()
        v = r["v"] + 1
        c.execute(
            "INSERT INTO versions VALUES (?,?,?,?,?,?)", (lid, v, author, note, json.dumps(content), now())
        )
        c.execute("UPDATE lessons SET updated_at=? WHERE id=?", (now(), lid))
    return v


def get_version(lid: str, version: int | None = None) -> dict | None:
    with conn() as c:
        if version is None:
            r = c.execute(
                "SELECT * FROM versions WHERE lesson_id=? ORDER BY version DESC LIMIT 1", (lid,)
            ).fetchone()
        else:
            r = c.execute("SELECT * FROM versions WHERE lesson_id=? AND version=?", (lid, version)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["content"] = json.loads(d["content"])
    return d


def list_versions(lid: str) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT lesson_id, version, author, note, created_at FROM versions WHERE lesson_id=? ORDER BY version DESC",
            (lid,),
        ).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------- events
def log_event(lid: str, message: str, level: str = "info") -> None:
    with _write_lock, conn() as c:
        c.execute("INSERT INTO events (lesson_id, ts, level, message) VALUES (?,?,?,?)", (lid, now(), level, message))


def get_events(lid: str, limit: int = 200) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT ts, level, message FROM events WHERE lesson_id=? ORDER BY id DESC LIMIT ?", (lid, limit)
        ).fetchall()
    return [dict(r) for r in reversed(rows)]
