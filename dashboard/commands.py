
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from db.database import Database

VALID_COMMANDS = frozenset(
    {
        "skip",
        "pause",
        "resume",
        "play_track",
        "set_volume",
        "apply_server",
        "disconnect",
    }
)

class UnknownCommandError(ValueError):
    pass

@dataclass(slots=True, frozen=True)
class CommandRow:
    command_id: int
    command: str
    payload: dict[str, Any] | None
    requested_by: str | None
    requested_at: str
    executed_at: str | None
    result: str | None

def enqueue(
    db: Database,
    *,
    command: str,
    requested_by: str | None,
    payload: dict[str, Any] | None = None,
) -> int:
    if command not in VALID_COMMANDS:
        raise UnknownCommandError(command)
    cur = db.execute(
        "INSERT INTO dashboard_commands(command, payload, requested_by) VALUES(?,?,?)",
        (command, json.dumps(payload) if payload else None, requested_by),
    )
    return int(cur.lastrowid)

def pending(db: Database, limit: int = 10) -> list[CommandRow]:
    rows = db.fetchall(
        "SELECT * FROM dashboard_commands WHERE executed_at IS NULL "
        "ORDER BY command_id ASC LIMIT ?",
        (limit,),
    )
    return [_row(r) for r in rows]

def recent(db: Database, limit: int = 20) -> list[CommandRow]:
    rows = db.fetchall(
        "SELECT * FROM dashboard_commands ORDER BY command_id DESC LIMIT ?",
        (limit,),
    )
    return [_row(r) for r in rows]

def mark_done(db: Database, command_id: int, *, result: str = "ok") -> None:
    db.execute(
        "UPDATE dashboard_commands SET executed_at=CURRENT_TIMESTAMP, result=? WHERE command_id=?",
        (result, command_id),
    )

def _row(r) -> CommandRow:
    payload = None
    if r["payload"]:
        try:
            payload = json.loads(r["payload"])
        except (TypeError, ValueError):
            payload = None
    return CommandRow(
        command_id=int(r["command_id"]),
        command=r["command"],
        payload=payload,
        requested_by=r["requested_by"],
        requested_at=r["requested_at"],
        executed_at=r["executed_at"],
        result=r["result"],
    )
