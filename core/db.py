"""统一 SQLite 存储。"""

from __future__ import annotations

import asyncio
import json

import aiosqlite


class Database:
    def __init__(self, path: str):
        self._path = str(path)
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def connect(self):
        self._conn = await aiosqlite.connect(self._path)
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS playlists (
                sender_id TEXT NOT NULL,
                song_id   TEXT NOT NULL,
                platform  TEXT NOT NULL DEFAULT '',
                song_name TEXT NOT NULL,
                artists   TEXT NOT NULL DEFAULT '',
                added_at  TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (sender_id, song_id, platform)
            );
            CREATE TABLE IF NOT EXISTS group_config (
                group_id TEXT PRIMARY KEY,
                data     TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS kv (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        await self._conn.commit()

    async def close(self):
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    # ---------- 通用 ----------

    async def execute(self, sql: str, params: tuple = ()):
        async with self._lock:
            assert self._conn is not None
            await self._conn.execute(sql, params)
            await self._conn.commit()

    async def fetch_one(self, sql: str, params: tuple = ()):
        async with self._lock:
            assert self._conn is not None
            cur = await self._conn.execute(sql, params)
            return await cur.fetchone()

    async def fetch_all(self, sql: str, params: tuple = ()):
        async with self._lock:
            assert self._conn is not None
            cur = await self._conn.execute(sql, params)
            return await cur.fetchall()

    # ---------- 歌单 ----------

    async def playlist_add(
        self, sender_id: str, song_id: str, platform: str, song_name: str, artists: str
    ):
        await self.execute(
            """
            INSERT INTO playlists (sender_id, song_id, platform, song_name, artists, added_at)
            VALUES (?, ?, ?, ?, ?, datetime('now', 'localtime'))
            ON CONFLICT(sender_id, song_id, platform) DO UPDATE SET
                song_name = excluded.song_name,
                artists = excluded.artists,
                added_at = excluded.added_at
            """,
            (sender_id, song_id, platform, song_name, artists),
        )

    async def playlist_list(self, sender_id: str, limit: int = 20):
        return await self.fetch_all(
            "SELECT song_id, platform, song_name, artists, added_at FROM playlists "
            "WHERE sender_id=? ORDER BY added_at DESC LIMIT ?",
            (sender_id, limit),
        )

    async def playlist_remove(self, sender_id: str, index: int) -> str | None:
        rows = await self.playlist_list(sender_id, limit=999)
        if not rows or index < 1 or index > len(rows):
            return None
        row = rows[index - 1]
        await self.execute(
            "DELETE FROM playlists WHERE sender_id=? AND song_id=? AND platform=?",
            (sender_id, row[0], row[1]),
        )
        return f"{row[2]} - {row[3]}"

    async def playlist_clear(self, sender_id: str):
        await self.execute("DELETE FROM playlists WHERE sender_id=?", (sender_id,))

    # ---------- 群管配置 ----------

    async def get_group_data(self, group_id: str) -> dict:
        row = await self.fetch_one("SELECT data FROM group_config WHERE group_id=?", (group_id,))
        if not row:
            return {}
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            return {}

    async def set_group_data(self, group_id: str, data: dict):
        await self.execute(
            "INSERT INTO group_config (group_id, data) VALUES (?, ?) "
            "ON CONFLICT(group_id) DO UPDATE SET data = excluded.data",
            (group_id, json.dumps(data, ensure_ascii=False)),
        )

    # ---------- KV ----------

    async def kv_get(self, key: str):
        row = await self.fetch_one("SELECT value FROM kv WHERE key=?", (key,))
        return row[0] if row else None

    async def kv_set(self, key: str, value: str):
        await self.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
