from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable, TypeVar

from pydantic import BaseModel


T = TypeVar("T", bound=BaseModel)


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.migrate()

    def migrate(self) -> None:
        self.conn.executescript(
            """
            create table if not exists records (
              kind text not null,
              id text not null,
              body text not null,
              updated_at text not null default current_timestamp,
              primary key (kind, id)
            );
            create table if not exists events (
              id integer primary key autoincrement,
              kind text not null,
              body text not null,
              created_at text not null default current_timestamp
            );
            """
        )
        self.conn.commit()

    def upsert(self, kind: str, item: BaseModel) -> None:
        item_id = str(getattr(item, "id", kind))
        body = item.model_dump_json()
        self.conn.execute(
            """
            insert into records(kind, id, body) values (?, ?, ?)
            on conflict(kind, id) do update set body = excluded.body, updated_at = current_timestamp
            """,
            (kind, item_id, body),
        )
        self.conn.commit()

    def delete(self, kind: str, item_id: str) -> None:
        self.conn.execute("delete from records where kind = ? and id = ?", (kind, item_id))
        self.conn.commit()

    def list(self, kind: str, model: type[T]) -> list[T]:
        rows = self.conn.execute("select body from records where kind = ? order by updated_at", (kind,))
        return [model.model_validate_json(row["body"]) for row in rows]

    def append_event(self, kind: str, body: dict) -> None:
        self.conn.execute("insert into events(kind, body) values (?, ?)", (kind, json.dumps(body, default=str)))
        self.conn.commit()

    def append_event_body(self, kind: str, body: dict) -> int:
        cursor = self.conn.execute("insert into events(kind, body) values (?, ?)", (kind, json.dumps(body, default=str)))
        self.conn.commit()
        return int(cursor.lastrowid)

    def list_events(self, limit: int = 100) -> list[dict]:
        rows = self.conn.execute(
            "select id, kind, body, created_at from events order by id desc limit ?",
            (limit,),
        )
        events = []
        for row in reversed(rows.fetchall()):
            body = json.loads(row["body"])
            events.append({"id": row["id"], "kind": row["kind"], "body": body, "created_at": row["created_at"]})
        return events

    def replace_all(self, kind: str, items: Iterable[BaseModel]) -> None:
        self.conn.execute("delete from records where kind = ?", (kind,))
        for item in items:
            self.upsert(kind, item)
        self.conn.commit()
