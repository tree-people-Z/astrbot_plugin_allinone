"""astrbot_plugin_allinone 主入口。

以自然语言（LLM 函数调用）为主驱动：LLM 依据对话意图调度点歌/群管
等模块能力；传统指令默认关闭，可在配置中开启作为兜底。

点歌与群管理参考 astrbot_plugin_music / astrbot_plugin_qqadmin 的交互设计。
"""

from __future__ import annotations

from dataclasses import asdict

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from .core.buttons import ButtonAction, QQButtons
from .core.config import Config
from .core.core import Core
from .core.messages import card, lyrics_card, markdown_result
from .core.utils import (
    group_id_of,
    is_aiocqhttp,
    resolve_targets,
    sender_id_of,
    sender_name_of,
    truncate,
)
from .modules.group_admin import AdminModule
from .modules.music import MusicModule, Song

SIGN = "[allinone]"


class AllInOnePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.core = Core(context=context, config=Config(config if config is not None else {}))
        self.music = MusicModule(self.core)
        self.admin = AdminModule(self.core)
        self.buttons = QQButtons(self.core)

    async def initialize(self):
        await self.core.start()
        logger.info(
            f"{SIGN} 已加载：自然语言驱动（LLM 工具）为主，指令={'开' if self.commands_on() else '关'}"
        )

    async def terminate(self):
        await self.core.stop()

    def commands_on(self) -> bool:
        return self.core.cfg.bool("command_enable", False)

    def module_enabled(self, key: str) -> bool:
        return self.core.cfg.bool(key, True)

    def is_qq(self, event: AstrMessageEvent) -> bool:
        return is_aiocqhttp(event)

    async def _targets(self, event: AstrMessageEvent, target: str = "") -> list[str]:
        return await resolve_targets(event, target)

    async def _send_tool_text(
        self, event: AstrMessageEvent, text: str, actions: list[ButtonAction] | None = None
    ) -> str:
        await self.buttons.send(event, markdown_result(event, text), actions)
        return "结果已直接发送给用户，无需复述。"

    @filter.llm_tool(name="search_music")
    async def tool_search_music(self, event: AstrMessageEvent, keyword: str):
        """在QQ音乐搜索歌曲，返回候选列表（不发送音频）。

        Args:
            keyword(string): 歌名或歌手等关键词
        """
        songs = await self.music.search(truncate(keyword, 60))
        return await self._send_tool_text(
            event,
            self.music.format_songs(songs, sender_name_of(event)),
            [
                ButtonAction(f"播放第 {index} 首", "play_song", {"song": asdict(song)})
                for index, song in enumerate(songs[:4], 1)
            ]
            + [ButtonAction("我的歌单", "playlist")],
        )

    @filter.llm_tool(name="play_music")
    async def tool_play_music(self, event: AstrMessageEvent, keyword: str, index: int = 1):
        """搜索并播放歌曲，优先发送音乐卡片，失败时回退语音或链接。

        Args:
            keyword(string): 歌名或歌手等关键词
            index(number): 选择搜索结果中的第几首，从 1 开始，默认 1
        """
        if not self.module_enabled("music_enable"):
            return "点歌功能未启用。"
        songs = await self.music.search(truncate(keyword, 60))
        if not songs:
            return f"{sender_name_of(event)}，没有找到《{keyword}》相关歌曲。"
        chosen = songs[(max(1, int(index)) - 1) % len(songs)]
        return await self.music.send_song(event, chosen)

    @filter.llm_tool(name="query_lyrics")
    async def tool_query_lyrics(self, event: AstrMessageEvent, keyword: str):
        """查询并返回歌曲歌词。

        Args:
            keyword(string): 歌名
        """
        songs = await self.music.search(truncate(keyword, 60), 1)
        if not songs:
            return await self._send_tool_text(
                event, f"{sender_name_of(event)}，没有找到《{keyword}》。"
            )
        lyric = await self.music.lyrics_of(songs[0])
        text = lyrics_card(songs[0].name, truncate(lyric, 1200))
        return await self._send_tool_text(event, text)

    @filter.llm_tool(name="add_to_playlist")
    async def tool_add_playlist(self, event: AstrMessageEvent, keyword: str):
        """把歌曲加入当前用户的歌单。

        Args:
            keyword(string): 歌名
        """
        songs = await self.music.search(truncate(keyword, 60), 1)
        if not songs:
            return await self._send_tool_text(
                event, f"{sender_name_of(event)}，没有找到《{keyword}》。"
            )
        return await self._send_tool_text(event, await self.music.save_to_playlist(event, songs[0]))

    @filter.llm_tool(name="show_my_playlist")
    async def tool_show_playlist(self, event: AstrMessageEvent):
        """查看当前用户的歌单。"""
        return await self._send_tool_text(event, await self.music.show_playlist(event))

    @filter.llm_tool(name="play_from_playlist")
    async def tool_play_playlist(self, event: AstrMessageEvent, index: int):
        """播放歌单中指定序号的歌曲。

        Args:
            index(number): 歌单序号，从 1 开始
        """
        return await self.music.play_from_playlist(event, int(index))

    @filter.llm_tool(name="remove_from_playlist")
    async def tool_remove_playlist(self, event: AstrMessageEvent, index: int):
        """从歌单移除指定序号的歌曲。

        Args:
            index(number): 歌单序号，从 1 开始
        """
        removed = await self.core.db.playlist_remove(sender_id_of(event), int(index))
        owner = f"{sender_name_of(event)}，"
        return await self._send_tool_text(
            event, f"{owner}已移除《{removed}》" if removed else f"{owner}没有这条记录。"
        )

    @filter.llm_tool(name="ban_group_user")
    async def tool_ban_user(self, event: AstrMessageEvent, target: str = "", duration: int = 300):
        """禁言指定 QQ 群成员。

        Args:
            target(string): 目标群成员昵称或QQ号，留空默认发送者
            duration(number): 禁言秒数，例如 10 分钟填 600，默认 300
        """
        if not self.is_qq(event):
            return "群管仅支持 QQ 平台。"
        targets = await self._targets(event, target)
        return await self._send_tool_text(
            event, await self.admin.ban(event, int(duration), targets)
        )

    @filter.llm_tool(name="unban_group_user")
    async def tool_unban_user(self, event: AstrMessageEvent, target: str = ""):
        """解除指定 QQ 群成员的禁言。

        Args:
            target(string): 目标群成员昵称或QQ号，留空默认发送者
        """
        if not self.is_qq(event):
            return "群管仅支持 QQ 平台。"
        targets = await self._targets(event, target)
        return await self._send_tool_text(event, await self.admin.unban(event, targets))

    @filter.llm_tool(name="set_whole_ban")
    async def tool_whole_ban(self, event: AstrMessageEvent, enable: bool = True):
        """开启或关闭全员禁言。

        Args:
            enable(boolean): true 开启，false 关闭
        """
        if not self.is_qq(event):
            return "群管仅支持 QQ 平台。"
        return await self._send_tool_text(event, await self.admin.whole_ban(event, bool(enable)))

    @filter.llm_tool(name="kick_group_user")
    async def tool_kick_user(self, event: AstrMessageEvent, target: str = "", block: bool = False):
        """将指定 QQ 群成员踢出群聊。

        Args:
            target(string): 目标群成员昵称或QQ号
            block(boolean): 是否同时拉黑（拒绝再次加群）
        """
        if not self.is_qq(event):
            return "群管仅支持 QQ 平台。"
        targets = await self._targets(event, target)
        return await self._send_tool_text(event, await self.admin.kick(event, bool(block), targets))

    @filter.llm_tool(name="recall_group_messages")
    async def tool_recall(self, event: AstrMessageEvent, target: str = "", count: int = 10):
        """撤回群成员最近的消息。

        Args:
            target(string): 目标群成员昵称或QQ号，留空则撤回最近引用的消息
            count(number): 撤回条数，默认 10
        """
        if not self.is_qq(event):
            return "群管仅支持 QQ 平台。"
        targets = await self._targets(event, target)
        target_id = targets[0] if targets else ""
        return await self._send_tool_text(
            event, await self.admin.recall(event, int(count), target_id)
        )

    @filter.llm_tool(name="rename_group_member")
    async def tool_rename_member(self, event: AstrMessageEvent, target: str, card: str):
        """修改群成员的群名片（昵称）。

        Args:
            target(string): 目标群成员昵称或QQ号
            card(string): 新的群名片
        """
        if not self.is_qq(event):
            return "群管仅支持 QQ 平台。"
        targets = await self._targets(event, target)
        return await self._send_tool_text(
            event, await self.admin.set_member_card(event, truncate(card, 40), targets)
        )

    @filter.llm_tool(name="send_group_notice")
    async def tool_notice(self, event: AstrMessageEvent, content: str):
        """发布 QQ 群公告。

        Args:
            content(string): 公告内容
        """
        if not self.is_qq(event):
            return "群管仅支持 QQ 平台。"
        return await self._send_tool_text(
            event, await self.admin.send_notice(event, truncate(content, 600))
        )

    @filter.llm_tool(name="set_group_badwords")
    async def tool_set_badwords(self, event: AstrMessageEvent, words: str = ""):
        """设置群违禁词（命中后自动撤回并禁言）。

        Args:
            words(string): 违禁词，多个用空格或逗号分隔
        """
        if not self.is_qq(event):
            return "群管仅支持 QQ 平台。"
        tokens = [w for w in words.replace("，", " ").replace(",", " ").split() if w]
        if not tokens:
            return await self._send_tool_text(event, await self.admin.list_badwords(event))
        return await self._send_tool_text(event, await self.admin.set_badword(event, tokens))

    @filter.llm_tool(name="show_group_badwords")
    async def tool_show_badwords(self, event: AstrMessageEvent):
        """查看当前群的自定义违禁词与内置违禁词开关。"""
        return await self._send_tool_text(event, await self.admin.list_badwords(event))

    @filter.llm_tool(name="set_curfew")
    async def tool_set_curfew(self, event: AstrMessageEvent, start: str = "", end: str = ""):
        """设置宵禁时间段（到点自动禁言发言者）。

        Args:
            start(string): 开始时间，格式 HH:MM，如 22:00
            end(string): 结束时间，格式 HH:MM，如 07:00
        """
        if not self.is_qq(event):
            return "群管仅支持 QQ 平台。"
        return await self._send_tool_text(
            event, await self.admin.set_curfew(event, start or None, end or None)
        )

    @filter.llm_tool(name="disable_curfew")
    async def tool_disable_curfew(self, event: AstrMessageEvent):
        """关闭本群宵禁。"""
        if not self.is_qq(event):
            return "群管仅支持 QQ 平台。"
        return await self._send_tool_text(event, await self.admin.set_curfew(event, None, None))

    @filter.llm_tool(name="set_member_title")
    async def tool_set_title(self, event: AstrMessageEvent, target: str, title: str):
        """设置群成员专属头衔（需群主）。

        Args:
            target(string): 目标群成员昵称或QQ号
            title(string): 头衔内容，留空清除
        """
        if not self.is_qq(event):
            return "群管仅支持 QQ 平台。"
        targets = await self._targets(event, target)
        return await self._send_tool_text(
            event, await self.admin.set_special_title(event, truncate(title, 20), targets)
        )

    @filter.llm_tool(name="set_group_admin")
    async def tool_set_admin(self, event: AstrMessageEvent, target: str, enable: bool = True):
        """设置或取消群成员的管理员身份（需群主）。

        Args:
            target(string): 目标群成员昵称或QQ号
            enable(boolean): true 设置管理员，false 取消
        """
        if not self.is_qq(event):
            return "群管仅支持 QQ 平台。"
        targets = await self._targets(event, target)
        return await self._send_tool_text(
            event, await self.admin.set_admin_perm(event, bool(enable), targets)
        )

    @filter.llm_tool(name="rename_group")
    async def tool_rename_group(self, event: AstrMessageEvent, group_name: str):
        """修改当前 QQ 群名称。

        Args:
            group_name(string): 新群名
        """
        if not self.is_qq(event):
            return "群管仅支持 QQ 平台。"
        return await self._send_tool_text(
            event, await self.admin.set_group_name(event, truncate(group_name, 30))
        )

    @filter.llm_tool(name="set_group_portrait")
    async def tool_group_portrait(self, event: AstrMessageEvent):
        """使用当前消息引用的图片设置群头像（需管理员）。"""
        if not self.is_qq(event):
            return "群管仅支持 QQ 平台。"
        return await self._send_tool_text(event, await self.admin.set_group_portrait(event))

    @filter.llm_tool(name="list_group_members")
    async def tool_list_members(self, event: AstrMessageEvent):
        """查看本群成员概况。"""
        if not self.is_qq(event):
            return "群管仅支持 QQ 平台。"
        return await self._send_tool_text(event, await self.admin.group_members_info(event))

    @filter.llm_tool(name="allinone_help")
    async def tool_help(self, event: AstrMessageEvent):
        """列出本插件当前可用的能力（供自然语言使用时参考）。"""
        return await self._send_tool_text(event, self.capability_help(event))

    def capability_help(self, event: AstrMessageEvent) -> str:
        lines = []
        if self.module_enabled("music_enable"):
            lines.append("- 点歌：如“点首稻香”“搜一下周杰伦的歌”“把稻香加入歌单”")
        if self.module_enabled("admin_enable") and self.is_qq(event):
            lines += [
                "- 群管理：如“把张三禁言10分钟”“踢了李四”“全员禁言”“撤回他最近3条消息”",
                "- 群设置：如“设置违禁词 广告 加群”“宵禁 23:00 07:00”“改群名 摸鱼群”",
            ]
        if self.commands_on():
            lines.append("（传统指令也已开启：/点歌 /查歌词 /我的歌单 /群管帮助）")
        return card("🧩 聚合助手", "直接告诉我你想做什么：", "\n".join(lines))

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_qq_event(self, event: AstrMessageEvent):
        """QQ 事件监听：违禁词/刷屏/宵禁。

        普通自然语言对话和主动回复统一交给 AstrBot 默认 Agent 管线。
        """
        if getattr(event, "is_at_or_wake_command", False):
            return

        if self.module_enabled("admin_enable") and group_id_of(event):
            handled = await self.admin.auto_moderate(event)
            if not handled:
                handled = await self.admin.enforce_curfew(event)
            if handled:
                event.stop_event()

    @filter.on_llm_request()
    async def inject_capability_hint(self, event: AstrMessageEvent, req):
        """向 LLM 注入本插件能力提示，让模型知道可以调度哪些能力。"""
        if not self.core.cfg.bool("llm_capability_hint", True):
            return
        capabilities = []
        if self.module_enabled("music_enable"):
            capabilities.append(
                "点歌(search_music, play_music, query_lyrics, add_to_playlist, show_my_playlist)"
            )
        if self.module_enabled("admin_enable") and self.is_qq(event):
            capabilities.append(
                "群管(ban_group_user, kick_group_user, recall_group_messages, send_group_notice, "
                "set_group_badwords, set_curfew, rename_group …)"
            )
        if not capabilities:
            return
        hint = (
            "<allinone_capabilities>\n"
            "你可以调度以下插件能力，用户用自然语言表达相关意图时应主动调用对应工具：\n- "
            + "\n- ".join(capabilities)
            + "\n</allinone_capabilities>"
        )
        try:
            from astrbot.core.agent.message import TextPart

            req.extra_user_content_parts.append(TextPart(text=hint))
        except Exception:
            if getattr(req, "system_prompt", None) is not None:
                req.system_prompt += hint

    @filter.command("aio_action")
    async def cmd_button_action(self, event: AstrMessageEvent, token: str = ""):
        """QQ 原生按钮的临时指令入口，不受传统指令开关影响。"""
        event.stop_event()
        action, error = self.buttons.consume(event, token)
        if action is None:
            await self.buttons.error(event, error)
            return
        await self._execute_button(event, action)

    async def _execute_button(self, event: AstrMessageEvent, action: ButtonAction):
        if action.action in {"playlist", "play_song"} and not self.module_enabled("music_enable"):
            await self._send_tool_text(event, card("🧩 功能已关闭", "点歌当前未启用。"))
            return
        if action.action == "playlist":
            await self._send_tool_text(event, await self.music.show_playlist(event))
        elif action.action == "play_song":
            song = Song(**action.payload["song"])
            status = await self.music.send_song(event, song)
            await self._send_tool_text(event, status, [ButtonAction("我的歌单", "playlist")])
        elif action.action == "help":
            await self._send_tool_text(event, self.capability_help(event))

    @filter.command("点歌", alias={"点播"})
    async def cmd_song(self, event: AstrMessageEvent):
        """点歌：点歌 <歌名>，结尾加序号可直接选择结果"""
        if not self.commands_on():
            return
        keyword, index = self.music.parse_request(event.message_str)
        if not keyword:
            await self.buttons.send(
                event,
                markdown_result(
                    event,
                    f"{sender_name_of(event)}，用法：点歌 <歌名>（可结尾加序号，如：点歌 稻香 2）",
                ),
            )
            event.stop_event()
            return
        songs = await self.music.search(keyword)
        if not songs:
            await self.buttons.send(
                event,
                markdown_result(event, f"{sender_name_of(event)}，没有找到《{keyword}》相关歌曲。"),
            )
            event.stop_event()
            return
        if index is not None and 1 <= index <= len(songs):
            await self.buttons.send(
                event, markdown_result(event, await self.music.send_song(event, songs[index - 1]))
            )
            event.stop_event()
            return
        song = await self.music.pick_song(event, songs)
        if song is not None:
            await self.buttons.send(
                event, markdown_result(event, await self.music.send_song(event, song))
            )
        event.stop_event()

    @filter.command("查歌词")
    async def cmd_lyrics(self, event: AstrMessageEvent):
        """查歌词 <歌名>"""
        if not self.commands_on():
            return
        keyword, _ = self.music.parse_request(event.message_str)
        songs = await self.music.search(keyword, 1) if keyword else []
        if not songs:
            await self.buttons.send(event, markdown_result(event, "用法：查歌词 <歌名>"))
            event.stop_event()
            return
        lyric = await self.music.lyrics_of(songs[0])
        await self.buttons.send(
            event,
            markdown_result(
                event,
                lyrics_card(songs[0].name, truncate(lyric, 1200)),
            ),
        )
        event.stop_event()

    @filter.command("我的歌单")
    async def cmd_playlist_list(self, event: AstrMessageEvent):
        """查看我的歌单"""
        if not self.commands_on():
            return
        await self.buttons.send(
            event, markdown_result(event, await self.music.show_playlist(event))
        )
        event.stop_event()

    @filter.command("群管帮助")
    async def cmd_admin_help(self, event: AstrMessageEvent):
        """查看群管指令"""
        if not self.commands_on():
            return
        await self.buttons.send(event, markdown_result(event, self.admin.help_text()))
        event.stop_event()
