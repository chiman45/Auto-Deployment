"""SQLite-backed deployment history store."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DB_PATH = Path.home() / ".deployagent" / "history.db"


@dataclass
class Snapshot:
    id: int
    timestamp: str
    service: str
    config_hash: str
    resource_arns: dict
    task_definition: dict
    stack_parameters: dict
    status: str  # "pending" | "success" | "failed" | "rolled_back"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("""
        CREATE TABLE IF NOT EXISTS deployments (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp        TEXT    NOT NULL,
            service          TEXT    NOT NULL,
            config_hash      TEXT    NOT NULL,
            resource_arns    TEXT    NOT NULL DEFAULT '{}',
            task_definition  TEXT    NOT NULL DEFAULT '{}',
            stack_parameters TEXT    NOT NULL DEFAULT '{}',
            status           TEXT    NOT NULL DEFAULT 'pending'
        )
    """)
    con.commit()
    return con


def save_snapshot(
    service: str,
    config_hash: str,
    resource_arns: dict,
    task_definition: dict,
    stack_parameters: dict,
    status: str = "pending",
) -> int:
    """Insert a new deploy record. Returns the row id."""
    con = _connect()
    cur = con.execute(
        """
        INSERT INTO deployments
            (timestamp, service, config_hash, resource_arns, task_definition, stack_parameters, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now(timezone.utc).isoformat(),
            service,
            config_hash,
            json.dumps(resource_arns),
            json.dumps(task_definition),
            json.dumps(stack_parameters),
            status,
        ),
    )
    con.commit()
    return cur.lastrowid  # type: ignore[return-value]


def update_status(deploy_id: int, status: str) -> None:
    con = _connect()
    con.execute("UPDATE deployments SET status = ? WHERE id = ?", (status, deploy_id))
    con.commit()


def get_last_good(service: str, steps: int = 1) -> Optional[Snapshot]:
    """
    Return the Nth-last successful snapshot for `service` (steps=1 → most recent).
    Returns None when no qualifying record exists.
    """
    con = _connect()
    rows = con.execute(
        """
        SELECT * FROM deployments
        WHERE service = ? AND status = 'success'
        ORDER BY id DESC
        LIMIT ?
        """,
        (service, steps),
    ).fetchall()

    if not rows or len(rows) < steps:
        return None

    row = rows[-1]
    return _row_to_snapshot(row)


def list_deployments(service: Optional[str] = None, limit: int = 10) -> list[Snapshot]:
    con = _connect()
    if service:
        rows = con.execute(
            "SELECT * FROM deployments WHERE service = ? ORDER BY id DESC LIMIT ?",
            (service, limit),
        ).fetchall()
    else:
        rows = con.execute(
            "SELECT * FROM deployments ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_to_snapshot(r) for r in rows]


def _row_to_snapshot(row: sqlite3.Row) -> Snapshot:
    return Snapshot(
        id=row["id"],
        timestamp=row["timestamp"],
        service=row["service"],
        config_hash=row["config_hash"],
        resource_arns=json.loads(row["resource_arns"]),
        task_definition=json.loads(row["task_definition"]),
        stack_parameters=json.loads(row["stack_parameters"]),
        status=row["status"],
    )
