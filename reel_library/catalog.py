"""SQLite metadata and path checks for an immutable source media directory."""

import json
import re
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

AssetType = Literal["image", "video", "audio"]
PATH_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")
MAX_TAGS_PER_ASSET = 24
SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL CHECK (type IN ('image', 'video', 'audio')),
    project TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    tags TEXT NOT NULL CHECK (json_valid(tags)),
    deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS assets_recent ON assets(created_at DESC, id) WHERE deleted_at IS NULL;
"""


class CatalogError(ValueError):
    """An actionable error that is safe to send to an MCP client."""


def utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def check_relative_path(value: str) -> str:
    segments = value.split("/")
    if len(segments) > 8 or any(not PATH_SEGMENT.fullmatch(part) for part in segments):
        raise CatalogError(
            "Use a library-relative path with '/' separators and no '.' or '..' segments."
        )
    return value


@dataclass(frozen=True, slots=True)
class Search:
    tag: str | None = None
    type: AssetType | None = None
    project: str | None = None
    path_prefix: str | None = None
    created_from: datetime | None = None
    created_before: datetime | None = None
    limit: int = 10
    offset: int = 0


class Catalog:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir.resolve()
        self.media_dir = self.data_dir / "media"
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "catalog.sqlite3"
        with self._connection() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connection(self, *, write=False):
        with closing(sqlite3.connect(self.db_path, timeout=1)) as conn, conn:
            conn.row_factory = sqlite3.Row
            if write:
                conn.execute("BEGIN IMMEDIATE")
            yield conn

    def seed(self, rows: list[dict], *, replace=False) -> None:
        with self._connection(write=True) as conn:
            if replace:
                conn.execute("DELETE FROM assets")
            conn.executemany(
                """INSERT INTO assets (id, type, project, path, created_at, tags)
                   VALUES (:id, :type, :project, :path, :created_at, :tags)
                   ON CONFLICT (id) DO NOTHING""",
                [dict(row, tags=json.dumps(row["tags"])) for row in rows],
            )

    def source_path(self, relative_path: str, *, must_exist=True) -> Path:
        path = self.media_dir / check_relative_path(relative_path)
        try:
            resolved = path.resolve(strict=must_exist)
        except (FileNotFoundError, RuntimeError) as exc:
            raise CatalogError(
                "The media file is unavailable. Ask the operator to restore demo data."
            ) from exc
        if not resolved.is_relative_to(self.media_dir) or (must_exist and not resolved.is_file()):
            raise CatalogError("The media path must resolve to a file inside the library.")
        return resolved

    @staticmethod
    def _asset(row: sqlite3.Row) -> dict:
        asset = dict(row)
        asset.pop("deleted_at")
        asset["tags"] = json.loads(asset["tags"])
        return asset

    @staticmethod
    def _require_active(conn: sqlite3.Connection, asset_id: str) -> sqlite3.Row:
        asset = conn.execute(
            "SELECT * FROM assets WHERE id = ? AND deleted_at IS NULL", (asset_id,)
        ).fetchone()
        if asset is None:
            raise CatalogError(
                "No active asset with that ID. Use search_assets to find an active ID."
            )
        return asset

    def get(self, asset_id: str) -> dict:
        with self._connection() as conn:
            return self._asset(self._require_active(conn, asset_id))

    def search(self, filters: Search) -> dict:
        terms = ["deleted_at IS NULL"]
        values = []
        if filters.tag is not None:
            terms.append("EXISTS (SELECT 1 FROM json_each(assets.tags) WHERE value = ?)")
            values.append(filters.tag.lower())
        if filters.type is not None:
            terms.append("type = ?")
            values.append(filters.type)
        if filters.project is not None:
            terms.append("project = ? COLLATE NOCASE")
            values.append(filters.project)
        if filters.path_prefix is not None:
            prefix = check_relative_path(filters.path_prefix) + "/"
            terms.append("substr(path, 1, ?) = ?")
            values.extend((len(prefix), prefix))
        if filters.created_from is not None:
            terms.append("created_at >= ?")
            values.append(utc_text(filters.created_from))
        if filters.created_before is not None:
            terms.append("created_at < ?")
            values.append(utc_text(filters.created_before))
        values.extend((filters.limit + 1, filters.offset))
        predicate = " AND ".join(terms)
        with self._connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM assets WHERE {predicate} ORDER BY created_at DESC, id LIMIT ? OFFSET ?",
                values,
            ).fetchall()
        return {
            "assets": [self._asset(row) for row in rows[: filters.limit]],
            "next_offset": filters.offset + filters.limit if len(rows) > filters.limit else None,
        }

    def update_tags(self, asset_id: str, *, add: list[str], remove: list[str]) -> dict:
        with self._connection(write=True) as conn:
            asset = self._asset(self._require_active(conn, asset_id))
            tags = sorted((set(asset["tags"]) | set(add)) - set(remove))
            if len(tags) > MAX_TAGS_PER_ASSET:
                raise CatalogError(
                    f"An asset can have up to {MAX_TAGS_PER_ASSET} tags. Remove a tag first."
                )
            conn.execute("UPDATE assets SET tags = ? WHERE id = ?", (json.dumps(tags), asset_id))
            return dict(asset, tags=tags)

    def soft_delete(self, asset_id: str) -> dict:
        when = utc_text(datetime.now(UTC))
        with self._connection(write=True) as conn:
            self._require_active(conn, asset_id)
            conn.execute("UPDATE assets SET deleted_at = ? WHERE id = ?", (when, asset_id))
        return {"asset_id": asset_id, "state": "soft_deleted", "deleted_at": when}

    def statistics(self) -> dict:
        with self._connection() as conn:
            rows = conn.execute(
                """SELECT project, type, COUNT(*) AS total FROM assets
                   WHERE deleted_at IS NULL GROUP BY project, type ORDER BY project, type"""
            ).fetchall()
        projects: dict[str, int] = {}
        types = {"image": 0, "video": 0, "audio": 0}
        for row in rows:
            projects[row["project"]] = projects.get(row["project"], 0) + row["total"]
            types[row["type"]] += row["total"]
        return {"total": sum(types.values()), "by_type": types, "by_project": projects}
