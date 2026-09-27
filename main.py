"""astrbot_plugin_allinone 主入口。

以自然语言（LLM 函数调用）为主驱动：LLM 依据对话意图调度签到/老婆/点歌/戳一戳/群管
等模块能力；传统指令默认关闭，可在配置中开启作为兜底。

灵感来源：@cvEvthBot 的签到与每日老婆玩法，以及
astrbot_plugin_music / astrbot_plugin_pokepro / astrbot_plugin_qqadmin 的交互设计。
"""

from __future__ import annotations

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from .core.config import Config
from .core.core import Core
from .core.utils import (
    group_id_of,
    is_aiocqhttp,
    resolve_targets,
    sender_id_of,
    truncate,
)
from .modules.checkin_wife import CheckinWifeModule
from .modules.group_admin import AdminModule
from .modules.music import MusicModule
from .modules.poke import PokeFactory

SIGN = "[allinone]"


class AllInOnePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.core = Core(context=context, config=Config(config if config is not None else {}))
        self.checkin_wife = CheckinWifeModule(self.core)
        self.music = MusicModule(self.core)
        self.poke = PokeFactory(self.core)
        self.admin = AdminModule(self.core)

    async def initialize(self):
        await self.core.start()
        logger.info(
            f"{SIGN} 已加载：自然语言驱动（LLM 工具）为主，指令={'开' if self.commands_on() else '关'}"
        )

    async def terminate(self):
        await self.core.stop()

    # ---------- 开关与判定 ----------

    def commands_on(self) -> bool:
        return self.core.cfg.bool("command_enable", False)

    def llm_on(self) -> bool:
        return self.core.cfg.bool("llm_enable", True)

    def module_enabled(self, key: str) -> bool:
        return self.core.cfg.bool(key, True)

    def is_qq(self, event: AstrMessageEvent) -> bool:
        return is_aiocqhttp(event)

    async def _targets(self, event: AstrMessageEvent, target: str = "") -> list[str]:
        return await resolve_targets(event, target)

    # ============================================================
    #  LLM 工具（主入口）
    # ============================================================

    # ----- 签到 / 积分 -----

    @filter.llm_tool(name="checkin")
    async def tool_checkin(self, event: AstrMessageEvent):
        """为当前用户执行每日签到，返回吉凶运势与获得积分结果。"""
        if not self.module_enabled("checkin_enable"):
            return "签到功能未启用。"
        return await self.checkin_wife.checkin(event)

    @filter.llm_tool(name="query_my_info")
    async def tool_my_info(self, event: AstrMessageEvent):
        """查询当前用户的累计积分、连续签到天数与今日老婆。"""
        return await self.checkin_wife.my_info(event)

    @filter.llm_tool(name="show_leaderboard")
    async def tool_leaderboard(self, event: AstrMessageEvent):
        """查看积分排行榜前 10 名。"""
        return await self.checkin_wife.leaderboard(event)

    # ----- 每日老婆 -----

    @filter.llm_tool(name="draw_daily_wife")
    async def tool_draw_wife(self, event: AstrMessageEvent):
        """抽取今日老婆（每人每天一次），会直接发送老婆图片与资料。"""
        if not self.module_enabled("wife_enable"):
            return "每日老婆功能未启用。"
        chain = await self.checkin_wife.wife(event)
        if not chain:
            return "老婆召唤失败，请稍后再试。"
        await event.send(event.chain_result(chain))
        return "已为用户抽取并发送今日老婆。"

    @filter.llm_tool(name="change_daily_wife")
    async def tool_change_wife(self, event: AstrMessageEvent):
        """为用户重新抽取今日老婆（会消耗积分）。"""
        if not self.module_enabled("wife_enable"):
            return "每日老婆功能未启用。"
        result = await self.checkin_wife.change_wife(event)
        if isinstance(result, list):
            await event.send(event.chain_result(result))
            return "已为用户换到新的老婆。"
        return str(result)

    # ----- 点歌（QQ音乐） -----

    @filter.llm_tool(name="search_music")
    async def tool_search_music(self, event: AstrMessageEvent, keyword: str):
        """在QQ音乐搜索歌曲，返回候选列表（不发送音频）。

        Args:
            keyword(string): 歌名或歌手等关键词
        """
        songs = await self.music.search(truncate(keyword, 60))
        return self.music.format_songs(songs)

    @filter.llm_tool(name="play_music")
    async def tool_play_music(self, event: AstrMessageEvent, keyword: str, index: int = 1):
        """搜索并播放歌曲，会直接发送歌曲信息与链接（可选语音）。

        Args:
            keyword(string): 歌名或歌手等关键词
            index(number): 选择搜索结果中的第几首，从 1 开始，默认 1
        """
        if not self.module_enabled("music_enable"):
            return "点歌功能未启用。"
        songs = await self.music.search(truncate(keyword, 60))
        if not songs:
            return f"没有找到《{keyword}》相关歌曲。"
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
            return f"没有找到《{keyword}》。"
        lyric = await self.music.lyrics_of(songs[0])
        return lyric or "未找到歌词。"

    @filter.llm_tool(name="add_to_playlist")
    async def tool_add_playlist(self, event: AstrMessageEvent, keyword: str):
        """把歌曲加入当前用户的歌单。

        Args:
            keyword(string): 歌名
        """
        songs = await self.music.search(truncate(keyword, 60), 1)
        if not songs:
            return f"没有找到《{keyword}》。"
        return await self.music.save_to_playlist(event, songs[0])

    @filter.llm_tool(name="show_my_playlist")
    async def tool_show_playlist(self, event: AstrMessageEvent):
        """查看当前用户的歌单。"""
        return await self.music.show_playlist(event)

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
        name = await self.core.db.playlist_remove(sender_id_of(event), int(index))
        return f"已移除《{name}》" if name else "没有这条记录。"

    # ----- 戳一戳（QQ） -----

    @filter.llm_tool(name="poke_user")
    async def tool_poke_user(self, event: AstrMessageEvent, target: str = "", times: int = 1):
        """戳一戳指定用户（QQ）。

        Args:
            target(string): 目标群成员昵称或QQ号，留空默认戳发送者
            times(number): 戳的次数，默认 1
        """
        if not self.module_enabled("poke_enable"):
            return "戳一戳功能未启用。"
        if not self.is_qq(event):
            return "戳一戳仅支持 QQ 平台。"
        targets = await self._targets(event, target)
        if not targets:
            return "没有找到要戳的目标。"
        bounded = max(1, min(self.core.cfg.int("poke_max_times", 5), int(times) or 1))
        await self.poke.send_poke(event, targets, bounded)
        return f"已戳 {('、'.join(targets))} 共 {bounded} 次"

    # ----- 群管（QQ） -----

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
        return await self.admin.ban(event, int(duration), targets)

    @filter.llm_tool(name="unban_group_user")
    async def tool_unban_user(self, event: AstrMessageEvent, target: str = ""):
        """解除指定 QQ 群成员的禁言。

        Args:
            target(string): 目标群成员昵称或QQ号，留空默认发送者
        """
        if not self.is_qq(event):
            return "群管仅支持 QQ 平台。"
        targets = await self._targets(event, target)
        return await self.admin.unban(event, targets)

    @filter.llm_tool(name="set_whole_ban")
    async def tool_whole_ban(self, event: AstrMessageEvent, enable: bool = True):
        """开启或关闭全员禁言。

        Args:
            enable(boolean): true 开启，false 关闭
        """
        if not self.is_qq(event):
            return "群管仅支持 QQ 平台。"
        return await self.admin.whole_ban(event, bool(enable))

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
        return await self.admin.kick(event, bool(block), targets)

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
        return await self.admin.recall(event, int(count), target_id)

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
        return await self.admin.set_member_card(event, truncate(card, 40), targets)

    @filter.llm_tool(name="send_group_notice")
    async def tool_notice(self, event: AstrMessageEvent, content: str):
        """发布 QQ 群公告。

        Args:
            content(string): 公告内容
        """
        if not self.is_qq(event):
            return "群管仅支持 QQ 平台。"
        return await self.admin.send_notice(event, truncate(content, 600))

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
            return await self.admin.list_badwords(event)
        return await self.admin.set_badword(event, tokens)

    @filter.llm_tool(name="show_group_badwords")
    async def tool_show_badwords(self, event: AstrMessageEvent):
        """查看当前群的自定义违禁词与内置违禁词开关。"""
        return await self.admin.list_badwords(event)

    @filter.llm_tool(name="set_curfew")
    async def tool_set_curfew(self, event: AstrMessageEvent, start: str = "", end: str = ""):
        """设置宵禁时间段（到点自动禁言发言者）。

        Args:
            start(string): 开始时间，格式 HH:MM，如 22:00
            end(string): 结束时间，格式 HH:MM，如 07:00
        """
        if not self.is_qq(event):
            return "群管仅支持 QQ 平台。"
        return await self.admin.set_curfew(event, start or None, end or None)

    @filter.llm_tool(name="disable_curfew")
    async def tool_disable_curfew(self, event: AstrMessageEvent):
        """关闭本群宵禁。"""
        if not self.is_qq(event):
            return "群管仅支持 QQ 平台。"
        return await self.admin.set_curfew(event, None, None)

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
        return await self.admin.set_special_title(event, truncate(title, 20), targets)

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
        return await self.admin.set_admin_perm(event, bool(enable), targets)

    @filter.llm_tool(name="rename_group")
    async def tool_rename_group(self, event: AstrMessageEvent, group_name: str):
        """修改当前 QQ 群名称。

        Args:
            group_name(string): 新群名
        """
        if not self.is_qq(event):
            return "群管仅支持 QQ 平台。"
        return await self.admin.set_group_name(event, truncate(group_name, 30))

    @filter.llm_tool(name="set_group_portrait")
    async def tool_group_portrait(self, event: AstrMessageEvent):
        """使用当前消息引用的图片设置群头像（需管理员）。"""
        if not self.is_qq(event):
            return "群管仅支持 QQ 平台。"
        return await self.admin.set_group_portrait(event)

    @filter.llm_tool(name="list_group_members")
    async def tool_list_members(self, event: AstrMessageEvent):
        """查看本群成员概况。"""
        if not self.is_qq(event):
            return "群管仅支持 QQ 平台。"
        return await self.admin.group_members_info(event)

    # ----- 元信息 -----

    @filter.llm_tool(name="allinone_help")
    async def tool_help(self, event: AstrMessageEvent):
        """列出本插件当前可用的能力（供自然语言使用时参考）。"""
        return self.capability_help(event)

    def capability_help(self, event: AstrMessageEvent) -> str:
        lines = ["我可以为你做这些事（直接用自然语言告诉我就行）："]
        if self.module_enabled("checkin_enable"):
            lines.append("- 签到/查积分/看排行榜：如“帮我签到”“我多少积分”“排行榜”")
        if self.module_enabled("wife_enable"):
            lines.append("- 每日老婆：如“抽老婆”“换一个老婆”")
        if self.module_enabled("music_enable"):
            lines.append("- 点歌：如“点首稻香”“搜一下周杰伦的歌”“把稻香加入歌单”")
        if self.module_enabled("poke_enable") and self.is_qq(event):
            lines.append("- 戳一戳：如“戳一下张三”")
        if self.module_enabled("admin_enable") and self.is_qq(event):
            lines += [
                "- 群管理：如“把张三禁言10分钟”“踢了李四”“全员禁言”“撤回他最近3条消息”",
                "- 群设置：如“设置违禁词 广告 加群”“宵禁 23:00 07:00”“改群名 摸鱼群”",
            ]
        if self.commands_on():
            lines.append("（传统指令也已开启：/签到 /点歌 /老婆 /群管帮助 等）")
        return "\n".join(lines)

    # ============================================================
    #  事件监听（被戳反应 / 违禁词 / 刷屏 / 宵禁）
    # ============================================================

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_qq_event(self, event: AstrMessageEvent):
        """QQ 事件监听：戳一戳响应 + 违禁词/刷屏/宵禁"""
        if self.module_enabled("poke_enable"):
            info = self.poke.parse(event)
            if info:
                async for reply in self.poke.handle_poked(event, info):
                    if reply is not None:
                        yield reply
                event.stop_event()
                return

        if self.module_enabled("admin_enable") and group_id_of(event):
            handled = await self.admin.auto_moderate(event)
            if not handled:
                handled = await self.admin.enforce_curfew(event)
            if handled:
                event.stop_event()

    # ============================================================
    #  LLM 能力注入
    # ============================================================

    @filter.on_llm_request()
    async def inject_capability_hint(self, event: AstrMessageEvent, req):
        """向 LLM 注入本插件能力提示，让模型知道可以调度哪些能力。"""
        if not self.core.cfg.bool("llm_capability_hint", True):
            return
        capabilities = []
        if self.module_enabled("checkin_enable"):
            capabilities.append("签到/查询积分/排行榜(checkin, query_my_info, show_leaderboard)")
        if self.module_enabled("wife_enable"):
            capabilities.append("每日老婆(draw_daily_wife, change_daily_wife)")
        if self.module_enabled("music_enable"):
            capabilities.append(
                "点歌(search_music, play_music, query_lyrics, add_to_playlist, show_my_playlist)"
            )
        if self.module_enabled("poke_enable") and self.is_qq(event):
            capabilities.append("戳一戳(poke_user)")
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

    # ============================================================
    #  传统指令（默认关闭，command_enable=true 时启用）
    # ============================================================

    @filter.command("签到", alias={"打卡", "sign"})
    async def cmd_checkin(self, event: AstrMessageEvent):
        """每日签到，抽取吉凶运势并获得积分"""
        if not self.commands_on():
            return
        yield event.plain_result(await self.checkin_wife.checkin(event))
        event.stop_event()

    @filter.command("我的信息", alias={"我的积分"})
    async def cmd_my_info(self, event: AstrMessageEvent):
        """查看自己的积分、签到与今日老婆"""
        if not self.commands_on():
            return
        yield event.plain_result(await self.checkin_wife.my_info(event))
        event.stop_event()

    @filter.command("排行榜", alias={"排行", "积分排行"})
    async def cmd_leaderboard(self, event: AstrMessageEvent):
        """查看积分排行榜 Top10"""
        if not self.commands_on():
            return
        yield event.plain_result(await self.checkin_wife.leaderboard(event))
        event.stop_event()

    @filter.command("老婆", alias={"今日老婆", "每日老婆"})
    async def cmd_wife(self, event: AstrMessageEvent):
        """抽取今日老婆"""
        if not self.commands_on():
            return
        chain = await self.checkin_wife.wife(event)
        if chain:
            yield event.chain_result(chain)
        else:
            yield event.plain_result("老婆召唤失败，请稍后再试~")
        event.stop_event()

    @filter.command("换老婆", alias={"换个老婆"})
    async def cmd_change_wife(self, event: AstrMessageEvent):
        """消耗积分重新抽取今日老婆"""
        if not self.commands_on():
            return
        result = await self.checkin_wife.change_wife(event)
        yield (
            event.chain_result(result)
            if isinstance(result, list)
            else event.plain_result(str(result))
        )
        event.stop_event()

    @filter.command("点歌", alias={"点播"})
    async def cmd_song(self, event: AstrMessageEvent):
        """点歌：点歌 <歌名>，结尾加序号可直接选择结果"""
        if not self.commands_on():
            return
        keyword, index = self.music.parse_request(event.message_str)
        if not keyword:
            yield event.plain_result("用法：点歌 <歌名>（可结尾加序号，如：点歌 稻香 2）")
            event.stop_event()
            return
        songs = await self.music.search(keyword)
        if not songs:
            yield event.plain_result(f"没有找到《{keyword}》相关歌曲。")
            event.stop_event()
            return
        if index is not None and 1 <= index <= len(songs):
            yield event.plain_result(await self.music.send_song(event, songs[index - 1]))
            event.stop_event()
            return
        song = await self.music.pick_song(event, songs)
        if song is not None:
            yield event.plain_result(await self.music.send_song(event, song))
        event.stop_event()

    @filter.command("查歌词")
    async def cmd_lyrics(self, event: AstrMessageEvent):
        """查歌词 <歌名>"""
        if not self.commands_on():
            return
        keyword, _ = self.music.parse_request(event.message_str)
        songs = await self.music.search(keyword, 1) if keyword else []
        if not songs:
            yield event.plain_result("用法：查歌词 <歌名>")
            event.stop_event()
            return
        lyric = await self.music.lyrics_of(songs[0])
        yield event.plain_result(
            f"📃 {songs[0].name} 歌词：\n{truncate(lyric, 1200)}" if lyric else "未找到歌词。"
        )
        event.stop_event()

    @filter.command("我的歌单")
    async def cmd_playlist_list(self, event: AstrMessageEvent):
        """查看我的歌单"""
        if not self.commands_on():
            return
        yield event.plain_result(await self.music.show_playlist(event))
        event.stop_event()

    @filter.command("群管帮助")
    async def cmd_admin_help(self, event: AstrMessageEvent):
        """查看群管指令"""
        if not self.commands_on():
            return
        yield event.plain_result(self.admin.help_text())
        event.stop_event()

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.command("戳")
    async def cmd_poke(self, event: AstrMessageEvent):
        """戳 @某人 [次数] 或 戳全体成员"""
        if not self.commands_on() or not self.module_enabled("poke_enable"):
            return
        text = (event.message_str or "").strip()
        self_id = str(event.get_self_id())
        if "全体成员" in text and event.is_admin():
            targets = [t for t in await self.member_ids(event) if t != self_id]
        else:
            targets = await self._targets(event, "")
        if targets:
            await self.poke.send_poke(event, targets, self.poke_times(text))
        event.stop_event()

    async def member_ids(self, event: AstrMessageEvent) -> list[str]:
        try:
            members = await event.bot.get_group_member_list(group_id=int(group_id_of(event)))
            return [str(member.get("user_id", "")) for member in members]
        except Exception as exc:
            logger.warning(f"{SIGN} 获取群成员失败: {exc}")
            return []

    def poke_times(self, message_str: str) -> int:
        tokens = (message_str or "").split()
        if tokens and tokens[-1].isdigit():
            value = int(tokens[-1])
        else:
            value = 1
        max_times = self.core.cfg.int("poke_max_times", 5)
        return max(1, min(max_times, value))
