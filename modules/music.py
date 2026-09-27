"""点歌模块（QQ音乐源，聚合接口）。"""

from __future__ import annotations

import json
from dataclasses import dataclass

import aiohttp
import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from ..core.core import Core
from ..core.utils import sender_id_of, truncate

SIGN = "[allinone:music]"
ARK_CARD_URL = "https://apii.xianyuw.cn/api/v1/qq-musicArk"

COMMAND_HEADS = ("点歌", "点播", "qq点歌")


@dataclass
class Song:
    song_id: str
    name: str
    artists: str
    audio_url: str = ""
    link: str = ""
    lyrics: str = ""
    cover_url: str = ""
    platform: str = "QQ音乐"


class MusicModule:
    def __init__(self, core: Core):
        self.core = core

    # ---------- 解析 ----------

    def parse_request(self, text: str) -> tuple[str, int | None]:
        """解析 '点歌 xx' / 'qq点歌 xx' / '点歌 xx 2' -> (keyword, index)。"""
        text = (text or "").strip()
        for head in COMMAND_HEADS:
            if text.startswith(head):
                text = text[len(head) :].strip()
                break
        else:
            parts = text.split(None, 1)
            text = parts[1].strip() if len(parts) > 1 else ""

        index: int | None = None
        tokens = text.split()
        if len(tokens) >= 2 and tokens[-1].isdigit():
            index = int(tokens[-1])
            text = " ".join(tokens[:-1])
        return text.strip(), index

    # ---------- 搜索 ----------

    async def search(self, keyword: str, limit: int | None = None) -> list[Song]:
        keyword = (keyword or "").strip()
        if not keyword:
            return []
        limit = limit or self.core.cfg.int("music_song_limit", 5)
        base = self.core.cfg.str("music_agg_base_url", "https://music.txqq.pro/")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/132.0.0.0 Safari/537.36",
            "X-Requested-With": "XMLHttpRequest",
        }
        data = {"input": keyword, "filter": "name", "type": "qq", "page": 1}
        result = await self.core.http.post_json(base, data=data, headers=headers)
        songs_raw = ((result or {}).get("data")) or ((result or {}).get("songs")) or []
        songs: list[Song] = []
        for raw in songs_raw[: max(1, limit)]:
            songs.append(
                Song(
                    song_id=str(raw.get("songid") or ""),
                    name=str(raw.get("title") or "未知"),
                    artists=str(raw.get("author") or "未知"),
                    audio_url=str(raw.get("url") or ""),
                    link=str(raw.get("link") or ""),
                    lyrics=str(raw.get("lrc") or ""),
                    cover_url=str(raw.get("pic") or ""),
                )
            )
        return songs

    async def lyrics_of(self, song: Song) -> str:
        lyric = song.lyrics.lstrip("\ufeff").strip()
        if not lyric:
            return ""
        lines = lyric.splitlines()
        if len(lines) > 40:
            lines = lines[:40] + ["……"]
        return "\n".join(lines)

    # ---------- 选歌 ----------

    async def pick_song(
        self, event: AstrMessageEvent, songs: list[Song], timeout: int = 60
    ) -> Song | None:
        if not songs:
            return None
        if self.core.cfg.str("music_select_mode", "text") == "single" or len(songs) == 1:
            return songs[0]

        from astrbot.core.utils.session_waiter import (
            SessionController,
            session_waiter,
        )

        lines = [f"{position}. 《{song.name}》- {song.artists}" for position, song in enumerate(songs, 1)]
        lines.append(f"请回复序号选择歌曲（1-{len(songs)}），回复 取消 退出：")
        await event.send(event.plain_result("\n".join(lines)))

        selected: Song | None = None

        @session_waiter(timeout=timeout, record_history_chains=False)
        async def waiter(controller: SessionController, waiter_event: AstrMessageEvent):
            if waiter_event.get_sender_id() != event.get_sender_id():
                return
            text = waiter_event.message_str.strip()
            if text in ("取消", "退出"):
                controller.stop()
                return
            if text.isdigit() and 1 <= int(text) <= len(songs):
                nonlocal selected
                selected = songs[int(text) - 1]
                controller.stop()

        try:
            await waiter(event)
        except TimeoutError:
            await event.send(event.plain_result("选择超时，已退出选歌。"))
        except Exception as exc:
            logger.warning(f"{SIGN} 选歌会话异常: {exc}")
        return selected

    # ---------- 发送 ----------

    async def _send_onebot(self, event: AstrMessageEvent, segment: dict) -> bool:
        try:
            target = (
                {"user_id": event.get_sender_id()}
                if event.is_private_chat()
                else {"group_id": event.get_group_id()}
            )
            action = "send_private_msg" if event.is_private_chat() else "send_group_msg"
            call_action = getattr(event.bot, "call_action", None)
            if call_action is None:
                call_action = event.bot.api.call_action
            result = await call_action(action, message=[segment], **target)
            if not isinstance(result, dict):
                return True
            # AstrBot/OneBot adapters may return either the flattened data
            # object or a standard {status, retcode, data} response.
            if result.get("status") == "failed" or result.get("retcode", 0) not in (0, None):
                return False
            return True
        except Exception as exc:
            logger.warning(f"{SIGN} OneBot 音乐卡片发送失败: {exc}")
            return False

    async def _send_card(self, event: AstrMessageEvent, song: Song) -> bool:
        if not song.audio_url:
            return False
        segment = {
            "type": "music",
            "data": {
                "type": "custom",
                "url": song.audio_url,
                "audio": song.audio_url,
                "title": song.name,
                "content": song.artists,
                "image": song.cover_url,
                "singer": song.artists,
            },
        }
        if await self._send_onebot(event, segment):
            return True
        # Some AstrBot adapters expose message components but not call_action.
        try:
            await event.send(
                event.chain_result(
                    [
                        Comp.Music(
                            type="custom",
                            url=song.audio_url,
                            audio=song.audio_url,
                            title=song.name,
                            content=song.artists,
                            image=song.cover_url,
                        )
                    ]
                )
            )
            return True
        except Exception as exc:
            logger.warning(f"{SIGN} 音乐组件卡片发送失败: {exc}")
            return False

    async def _send_ark_card(self, event: AstrMessageEvent, song: Song, key: str) -> bool:
        if not song.audio_url or not song.cover_url:
            return False
        session = self.core.http.session
        if session is None:
            await self.core.http.start()
            session = self.core.http.session
        assert session is not None
        params = {
            "key": key,
            "url": song.audio_url,
            "song": song.name,
            "singer": song.artists,
            "cover": song.cover_url,
            "jump": song.audio_url,
            "format": "qq",
        }
        try:
            async with session.get(
                ARK_CARD_URL,
                params=params,
                proxy=self.core.cfg.str("http_proxy") or None,
            ) as response:
                if response.status != 200:
                    logger.warning(f"{SIGN} 签名卡片服务返回 HTTP {response.status}")
                    return False
                result = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            logger.warning(f"{SIGN} 签名卡片请求失败: {type(exc).__name__}")
            return False
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, dict) or result.get("code") != 200 or not {"app", "meta", "prompt", "view"} <= data.keys():
            logger.warning(f"{SIGN} 签名卡片服务返回无效数据")
            return False
        return await self._send_onebot(
            event, {"type": "json", "data": {"data": json.dumps(data, ensure_ascii=False)}}
        )

    async def send_song(self, event: AstrMessageEvent, song: Song) -> str:
        sent = False
        if event.get_platform_name() == "aiocqhttp" and self.core.cfg.bool("music_send_card", True):
            sent = await self._send_card(event, song)
            key = self.core.cfg.str("music_card_api_key").strip()
            if not sent and key:
                sent = await self._send_ark_card(event, song, key)
        if not sent and song.audio_url and self.core.cfg.bool("music_record_link", False):
            try:
                await event.send(event.chain_result([Comp.Record.fromURL(song.audio_url)]))
                sent = True
            except Exception as exc:
                logger.warning(f"{SIGN} 语音链接发送失败: {exc}")
        if not sent and (song.link or song.audio_url):
            try:
                info = f"🎵 {song.name} - {song.artists}\n🔗 {song.link or song.audio_url}"
                await event.send(event.plain_result(info))
                sent = True
            except Exception as exc:
                logger.warning(f"{SIGN} 歌曲链接发送失败: {exc}")
        if not sent:
            return f"歌曲《{song.name}》发送失败，暂无可用链接。"
        if self.core.cfg.bool("music_enable_lyrics", True):
            lyric = await self.lyrics_of(song)
            if lyric:
                try:
                    await event.send(event.plain_result(f"📃 歌词预览：\n{truncate(lyric, 600)}"))
                except Exception as exc:
                    logger.warning(f"{SIGN} 歌词发送失败: {exc}")
        return f"已发送歌曲《{song.name}》- {song.artists}"

    def format_songs(self, songs: list[Song]) -> str:
        if not songs:
            return "没有找到相关歌曲。"
        lines = ["找到以下歌曲："]
        for position, song in enumerate(songs, 1):
            lines.append(f"{position}. 《{song.name}》- {song.artists}")
        lines.append("回复序号可选择播放，例如：点歌 稻香 2")
        return "\n".join(lines)

    # ---------- 歌单 ----------

    async def save_to_playlist(self, event: AstrMessageEvent, song: Song) -> str:
        await self.core.db.playlist_add(
            sender_id_of(event), song.song_id, "qq", song.name, song.artists
        )
        return f"已将《{song.name}》加入歌单"

    async def show_playlist(self, event: AstrMessageEvent) -> str:
        rows = await self.core.db.playlist_list(sender_id_of(event), 20)
        if not rows:
            return "歌单还是空的，发送 歌单添加 <歌名> 收藏歌曲吧~"
        lines = ["🎵 我的歌单："]
        for position, row in enumerate(rows, 1):
            lines.append(f"{position}. {row[2]} - {row[3]}")
        lines.append("发送 播放歌单 <序号> 播放，删除歌单 <序号> 移除")
        return "\n".join(lines)

    async def play_from_playlist(self, event: AstrMessageEvent, index: int) -> str:
        rows = await self.core.db.playlist_list(sender_id_of(event), 20)
        if not rows or index < 1 or index > len(rows):
            return "歌单里没有这首歌，发送 我的歌单 查看列表。"
        row = rows[index - 1]
        songs = await self.search(row[2], 1)
        if not songs:
            return "这首歌暂时搜索不到，试试其他歌曲。"
        return await self.send_song(event, songs[0])
