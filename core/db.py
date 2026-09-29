"""统一 SQLite 存储。"""

from __future__ import annotations

import asyncio
import json

import aiosqlite


class Database:
    MIGRATION_KEY = "migration:user_anchor:v1"

    def __init__(self, path: str):
        self._path = str(path)
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def connect(self):
        self._conn = await aiosqlite.connect(self._path)
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                sender_id         TEXT NOT NULL,
                scope_id          TEXT NOT NULL DEFAULT '',
                points            INTEGER NOT NULL DEFAULT 0,
                streak            INTEGER NOT NULL DEFAULT 0,
                last_checkin_date TEXT,
                total_checkins    INTEGER NOT NULL DEFAULT 0,
                sender_name       TEXT,
                PRIMARY KEY (sender_id, scope_id)
            );
            CREATE TABLE IF NOT EXISTS daily_wife (
                sender_id      TEXT NOT NULL,
                group_id       TEXT NOT NULL DEFAULT '',
                date           TEXT NOT NULL,
                character_name TEXT,
                image_url      TEXT,
                source         TEXT,
                extra          TEXT,
                change_count   INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (sender_id, group_id, date)
            );
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
        await self._migrate_user_anchor()

    async def close(self):
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def _migrate_user_anchor(self):
        """一次性迁移：把按群存储的积分与老婆数据合并到用户级（scope_id/group_id=''）。"""
        if await self.kv_get(self.MIGRATION_KEY):
            return
        async with self._lock:
            assert self._conn is not None

            cur = await self._conn.execute(
                "SELECT sender_id, points, streak, last_checkin_date, total_checkins, sender_name "
                "FROM users WHERE scope_id != ''"
            )
            user_rows = await cur.fetchall()
            if user_rows:
                grouped: dict[str, dict] = {}
                for sender_id, points, streak, last_date, total, name in user_rows:
                    item = grouped.setdefault(
                        sender_id,
                        {"points": 0, "streak": 0, "last_date": None, "total": 0, "name": None},
                    )
                    item["points"] += int(points or 0)
                    item["total"] += int(total or 0)
                    if last_date and (item["last_date"] is None or last_date > item["last_date"]):
                        item["last_date"] = last_date
                        item["streak"] = int(streak or 0)
                        if name:
                            item["name"] = name
                    elif item["name"] is None and name:
                        item["name"] = name
                for sender_id, item in grouped.items():
                    await self._conn.execute(
                        """
                        INSERT INTO users
                            (sender_id, scope_id, points, streak, last_checkin_date,
                             total_checkins, sender_name)
                        VALUES (?, '', ?, ?, ?, ?, ?)
                        ON CONFLICT(sender_id, scope_id) DO UPDATE SET
                            points = points + excluded.points,
                            total_checkins = total_checkins + excluded.total_checkins,
                            streak = CASE
                                WHEN excluded.last_checkin_date IS NOT NULL
                                 AND (last_checkin_date IS NULL
                                      OR excluded.last_checkin_date >= last_checkin_date)
                                THEN excluded.streak ELSE streak END,
                            last_checkin_date = CASE
                                WHEN last_checkin_date IS NULL
                                  OR excluded.last_checkin_date > last_checkin_date
                                THEN excluded.last_checkin_date ELSE last_checkin_date END,
                            sender_name = COALESCE(excluded.sender_name, sender_name)
                        """,
                        (
                            sender_id,
                            item["points"],
                            item["streak"],
                            item["last_date"],
                            item["total"],
                            item["name"],
                        ),
                    )
                await self._conn.execute("DELETE FROM users WHERE scope_id != ''")

            cur = await self._conn.execute("SELECT sender_id, date FROM daily_wife WHERE group_id=''")
            global_keys = {(row[0], row[1]) for row in await cur.fetchall()}

            cur = await self._conn.execute(
                "SELECT sender_id, date, character_name, image_url, source, extra, change_count "
                "FROM daily_wife WHERE group_id != '' "
                "ORDER BY sender_id, date, change_count DESC, rowid DESC"
            )
            wife_rows = await cur.fetchall()
            chosen: dict[tuple[str, str], tuple] = {}
            for row in wife_rows:
                chosen.setdefault((row[0], row[1]), row)
            for key, row in chosen.items():
                sender_id, date = key
                if key in global_keys:
                    await self._conn.execute(
                        "DELETE FROM daily_wife WHERE sender_id=? AND date=? AND group_id != ''",
                        key,
                    )
                    continue
                await self._conn.execute(
                    "DELETE FROM daily_wife WHERE sender_id=? AND date=?", key
                )
                await self._conn.execute(
                    "INSERT INTO daily_wife "
                    "(sender_id, group_id, date, character_name, image_url, source, extra, change_count) "
                    "VALUES (?, '', ?, ?, ?, ?, ?, ?)",
                    (sender_id, date, row[2], row[3], row[4], row[5], row[6]),
                )

            await self._conn.commit()
        await self.kv_set(self.MIGRATION_KEY, "done")

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

    # ---------- 用户积分 ----------

    async def get_user(self, sender_id: str, scope_id: str):
        return await self.fetch_one(
            "SELECT points, streak, last_checkin_date, total_checkins, sender_name "
            "FROM users WHERE sender_id=? AND scope_id=?",
            (sender_id, scope_id),
        )

    async def apply_checkin(
        self, sender_id: str, scope_id: str, name: str, gain: int, streak: int, today: str
    ):
        await self.execute(
            """
            INSERT INTO users
                (sender_id, scope_id, points, streak, last_checkin_date, total_checkins, sender_name)
            VALUES (?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(sender_id, scope_id) DO UPDATE SET
                points = points + excluded.points,
                streak = excluded.streak,
                last_checkin_date = excluded.last_checkin_date,
                total_checkins = total_checkins + 1,
                sender_name = excluded.sender_name
            """,
            (sender_id, scope_id, gain, streak, today, name),
        )

    async def add_points(self, sender_id: str, scope_id: str, amount: int, name: str | None = None):
        await self.execute(
            """
            INSERT INTO users (sender_id, scope_id, points, sender_name)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(sender_id, scope_id) DO UPDATE SET
                points = points + excluded.points,
                sender_name = COALESCE(excluded.sender_name, sender_name)
            """,
            (sender_id, scope_id, amount, name),
        )

    async def leaderboard(self, scope_id: str, limit: int = 10):
        return await self.fetch_all(
            "SELECT sender_id, sender_name, points FROM users "
            "WHERE scope_id=? ORDER BY points DESC LIMIT ?",
            (scope_id, limit),
        )

    async def user_rank(self, scope_id: str, sender_id: str):
        """返回 (名次, 积分)；同分并列共享名次；无记录返回 None。"""
        row = await self.fetch_one(
            "SELECT points FROM users WHERE sender_id=? AND scope_id=?",
            (sender_id, scope_id),
        )
        if not row:
            return None
        points = int(row[0] or 0)
        rank_row = await self.fetch_one(
            "SELECT COUNT(*) + 1 FROM users WHERE scope_id=? AND points > ?",
            (scope_id, points),
        )
        return int(rank_row[0]), points

    # ---------- 每日老婆 ----------

    async def get_wife(self, sender_id: str, group_id: str, date: str):
        return await self.fetch_one(
            "SELECT character_name, image_url, source, extra, change_count "
            "FROM daily_wife WHERE sender_id=? AND group_id=? AND date=?",
            (sender_id, group_id, date),
        )

    async def set_wife(
        self,
        sender_id: str,
        group_id: str,
        date: str,
        name: str,
        image: str,
        source: str,
        extra: str,
        change_count: int,
    ):
        await self.execute(
            """
            INSERT INTO daily_wife
                (sender_id, group_id, date, character_name, image_url, source, extra, change_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sender_id, group_id, date) DO UPDATE SET
                character_name = excluded.character_name,
                image_url = excluded.image_url,
                source = excluded.source,
                extra = excluded.extra,
                change_count = excluded.change_count
            """,
            (sender_id, group_id, date, name, image, source, extra, change_count),
        )

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
