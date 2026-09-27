"""意图调度器：把自然语言决策出的意图路由到 modules 层。

新增功能的接法：
1. 在 modules/ 下写好模块方法（返回 str 或消息链 list）；
2. 在本文件 INTENT_TABLE 加一行映射；
3. 在 PROMPT_INTENTS 里补充意图说明供 LLM 决策。
"""

from __future__ import annotations

import json

from astrbot.api import AstrMessageEvent, logger

from .core import Core
from .utils import resolve_targets, truncate

SIGN = "[allinone:dispatch]"

PROMPT_INTENTS = (
    "checkin, points, leaderboard, wife, wife_change, "
    "music_search, music_play, music_lyrics, playlist_add, playlist_show, "
    "playlist_play, playlist_remove, poke, ban, unban, whole_ban, kick, "
    "recall, rename_member, notice, badwords_set, badwords_show, curfew_set, "
    "curfew_off, title, set_admin, rename_group, members, chat"
)

ARG_KEYS = (
    "keyword",
    "index",
    "target",
    "times",
    "duration",
    "card",
    "content",
    "words",
    "start",
    "end",
    "group_name",
    "enable",
    "block",
    "text",
)

DECISION_PROMPT = """你是群聊里的助手调度器。下面是本群最近的对话：

{history}

最新一条（来自 {name}）：{message}

判断这条消息是否需要你接手。若需要，从中选出意图并提取参数；若只是普通闲聊但值得搭话，选择 chat 并给出不超过25字的俏皮回复；觉得没必要回应则选 none。

可选意图：{intents}

只输出一行 JSON（不要多余文字、不要代码块），形如：
{{"reply": true, "intent": "music_play", "args": {{"keyword": "稻香", "index": 1}}}}
不需要接手时输出：{{"reply": false, "intent": "none"}}"""


class Dispatcher:
    def __init__(self, core: Core, modules: dict):
        self.core = core
        self.checkin_wife = modules["checkin_wife"]
        self.music = modules["music"]
        self.poke = modules["poke"]
        self.admin = modules["admin"]

    @staticmethod
    def decision_prompt(history: str, name: str, message: str) -> str:
        return DECISION_PROMPT.format(
            history=history, name=name, message=message, intents=PROMPT_INTENTS
        )

    def parse_decision(self, raw: str) -> dict:
        text = (raw or "").strip()
        if text.startswith("```"):
            text = text.strip("`").lstrip("json").strip()
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return {"reply": False, "intent": "none"}
        if not isinstance(data, dict):
            return {"reply": False, "intent": "none"}
        intent = str(data.get("intent") or "none")
        args_raw = data.get("args") or {}
        args: dict = {}
        if isinstance(args_raw, dict):
            args = {k: args_raw[k] for k in ARG_KEYS if k in args_raw}
        return {"reply": bool(data.get("reply")), "intent": intent, "args": args}

    async def targets_of(self, event: AstrMessageEvent, spec: str = "") -> list[str]:
        return await resolve_targets(event, spec)

    async def dispatch(self, event: AstrMessageEvent, intent: str, args: dict):
        """执行意图，返回 str | list(chain) | None。"""
        try:
            handler = getattr(self, f"do_{intent}", None)
            if handler is None:
                return f"未知意图：{intent}"
            return await handler(event, args)
        except Exception as exc:
            logger.warning(f"{SIGN} 意图 {intent} 执行失败: {exc}")
            return f"执行失败：{truncate(str(exc), 80)}"

    # ---------- 签到 / 积分 ----------

    async def do_checkin(self, event, args):
        return await self.checkin_wife.checkin(event)

    async def do_points(self, event, args):
        return await self.checkin_wife.my_info(event)

    async def do_leaderboard(self, event, args):
        return await self.checkin_wife.leaderboard(event)

    ## ---------- 每日老婆 ----------

    async def do_wife(self, event, args):
        chain = await self.checkin_wife.wife(event)
        return chain if chain else "老婆召唤失败，请稍后再试~"

    async def do_wife_change(self, event, args):
        result = await self.checkin_wife.change_wife(event)
        return result

    # ---------- 点歌 ----------

    async def do_music_search(self, event, args):
        songs = await self.music.search(truncate(str(args.get("keyword", "")), 60))
        return self.music.format_songs(songs)

    async def do_music_play(self, event, args):
        keyword = truncate(str(args.get("keyword", "")), 60)
        if not keyword:
            return "想听哪首歌呢？告诉我歌名吧~"
        songs = await self.music.search(keyword)
        if not songs:
            return f"没有找到《{keyword}》相关歌曲。"
        index = int(args.get("index") or 1)
        chosen = songs[(max(1, index) - 1) % len(songs)]
        return await self.music.send_song(event, chosen)

    async def do_music_lyrics(self, event, args):
        keyword = truncate(str(args.get("keyword", "")), 60)
        songs = await self.music.search(keyword, 1)
        if not songs:
            return f"没有找到《{keyword}》。"
        lyric = await self.music.lyrics_of(songs[0])
        return lyric if lyric else "未找到歌词。"

    async def do_playlist_add(self, event, args):
        keyword = str(args.get("keyword", ""))
        songs = await self.music.search(truncate(keyword, 60), 1)
        if not songs:
            return f"没有找到《{keyword}》。"
        return await self.music.save_to_playlist(event, songs[0])

    async def do_playlist_show(self, event, args):
        return await self.music.show_playlist(event)

    async def do_playlist_play(self, event, args):
        return await self.music.play_from_playlist(event, int(args.get("index") or 1))

    async def do_playlist_remove(self, event, args):
        from .utils import sender_id_of

        removed = await self.core.db.playlist_remove(
            sender_id_of(event), int(args.get("index") or 1)
        )
        return f"已移除《{removed}》" if removed else "没有这条记录。"

    # ---------- 戳一戳 ----------

    async def do_poke(self, event, args):
        if not self.core.cfg.bool("poke_enable", True):
            return "戳一戳功能未启用。"
        targets = await self.targets_of(event, str(args.get("target", "")))
        if not targets:
            return "没有找到要戳的目标。"
        bounded = max(1, min(self.core.cfg.int("poke_max_times", 5), int(args.get("times") or 1)))
        await self.poke.send_poke(event, targets, bounded)
        return f"已戳 {'、'.join(targets)} {bounded} 次"

    # ---------- 群管 ----------

    def _qq_only(self, event):
        from .utils import is_aiocqhttp

        if not self.core.cfg.bool("admin_enable", True):
            return "群管模块未启用。"
        if not is_aiocqhttp(event):
            return "群管仅支持 QQ 平台。"
        return None

    async def do_ban(self, event, args):
        gate = self._qq_only(event)
        if gate:
            return gate
        targets = await self.targets_of(event, str(args.get("target", "")))
        duration = max(1, int(args.get("duration") or 300))
        return await self.admin.ban(event, duration, targets)

    async def do_unban(self, event, args):
        gate = self._qq_only(event)
        if gate:
            return gate
        targets = await self.targets_of(event, str(args.get("target", "")))
        return await self.admin.unban(event, targets)

    async def do_whole_ban(self, event, args):
        gate = self._qq_only(event)
        if gate:
            return gate
        enable = str(args.get("enable", "true")).lower() not in ("false", "0", "no", "off")
        return await self.admin.whole_ban(event, enable)

    async def do_kick(self, event, args):
        gate = self._qq_only(event)
        if gate:
            return gate
        targets = await self.targets_of(event, str(args.get("target", "")))
        block = str(args.get("block", "false")).lower() in ("true", "1", "yes")
        return await self.admin.kick(event, block, targets)

    async def do_recall(self, event, args):
        gate = self._qq_only(event)
        if gate:
            return gate
        targets = await self.targets_of(event, str(args.get("target", "")))
        target_id = targets[0] if targets else ""
        return await self.admin.recall(event, int(args.get("times") or 10), target_id)

    async def do_rename_member(self, event, args):
        gate = self._qq_only(event)
        if gate:
            return gate
        targets = await self.targets_of(event, str(args.get("target", "")))
        card = truncate(str(args.get("card") or args.get("text") or ""), 40)
        return await self.admin.set_member_card(event, card, targets)

    async def do_notice(self, event, args):
        gate = self._qq_only(event)
        if gate:
            return gate
        content = truncate(str(args.get("content") or args.get("text") or ""), 600)
        return await self.admin.send_notice(event, content)

    async def do_badwords_set(self, event, args):
        gate = self._qq_only(event)
        if gate:
            return gate
        raw = str(args.get("words") or args.get("text") or "")
        tokens = [w for w in raw.replace("，", " ").replace(",", " ").split() if w]
        if not tokens:
            return await self.admin.list_badwords(event)
        return await self.admin.set_badword(event, tokens)

    async def do_badwords_show(self, event, args):
        gate = self._qq_only(event)
        if gate:
            return gate
        return await self.admin.list_badwords(event)

    async def do_curfew_set(self, event, args):
        gate = self._qq_only(event)
        if gate:
            return gate
        return await self.admin.set_curfew(event, args.get("start"), args.get("end"))

    async def do_curfew_off(self, event, args):
        gate = self._qq_only(event)
        if gate:
            return gate
        return await self.admin.set_curfew(event, None, None)

    async def do_title(self, event, args):
        gate = self._qq_only(event)
        if gate:
            return gate
        targets = await self.targets_of(event, str(args.get("target", "")))
        title = truncate(str(args.get("card") or args.get("text") or ""), 20)
        return await self.admin.set_special_title(event, title, targets)

    async def do_set_admin(self, event, args):
        gate = self._qq_only(event)
        if gate:
            return gate
        targets = await self.targets_of(event, str(args.get("target", "")))
        enable = str(args.get("enable", "true")).lower() not in ("false", "0", "no", "off")
        return await self.admin.set_admin_perm(event, enable, targets)

    async def do_rename_group(self, event, args):
        gate = self._qq_only(event)
        if gate:
            return gate
        name = truncate(str(args.get("group_name") or args.get("text") or ""), 30)
        return await self.admin.set_group_name(event, name)

    async def do_members(self, event, args):
        gate = self._qq_only(event)
        if gate:
            return gate
        return await self.admin.group_members_info(event)

    # ---------- 仅聊天 ----------

    async def do_chat(self, event, args):
        text = truncate(str(args.get("text") or ""), 120)
        if not text:
            return None
        return text
