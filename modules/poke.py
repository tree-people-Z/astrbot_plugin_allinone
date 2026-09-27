"""戳一戳模块（仅 aiocqhttp / QQ）。"""

from __future__ import annotations

import asyncio
import random
import time

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import At, Face

from ..core.core import Core
from ..core.utils import group_id_of, sender_id_of, sender_name_of

SIGN = "[allinone:poke]"


class Cooldown:
    def __init__(self):
        self.user_last: dict[str, float] = {}
        self.group_last: dict[str, float] = {}

    def allow_user(self, user_id: str, cd: float) -> bool:
        return self.check(self.user_last, user_id, cd)

    def allow_group(self, group_id: str, cd: float) -> bool:
        return cd <= 0 or self.check(self.group_last, group_id, cd)

    def mark_user(self, user_id: str):
        self.user_last[user_id] = time.time()

    def check(self, table: dict, key: str, cd: float) -> bool:
        now_stamp = time.time()
        last = table.get(key)
        if last is not None and now_stamp - last < cd:
            return False
        return True


class PokeEventInfo:
    def __init__(self, raw: dict):
        self.time = int(raw.get("time", 0) or 0)
        self.self_id = str(raw.get("self_id", "") or "")
        self.user_id = str(raw.get("user_id", "") or "")
        self.target_id = str(raw.get("target_id", "") or "")
        self.group_id = raw.get("group_id")
        self.raw = raw

    @property
    def is_group_poke(self) -> bool:
        return self.group_id is not None

    @property
    def is_self_poked(self) -> bool:
        return self.self_id == self.target_id

    @property
    def is_self_send(self) -> bool:
        return self.user_id == self.self_id


class PokeFactory:
    def __init__(self, core: Core):
        self.core = core
        self.cooldown = Cooldown()

    async def get_conversation(self, event: AstrMessageEvent):
        context = getattr(self.core, "context", None)
        conv_mgr = getattr(context, "conversation_manager", None)
        if conv_mgr is None:
            return None
        try:
            curr = await conv_mgr.get_curr_conversation_id(event.unified_msg_origin)
            return await conv_mgr.get_conversation(event.unified_msg_origin, curr)
        except Exception as exc:
            logger.warning(f"{SIGN} 获取会话失败: {exc}")
            return None

    @staticmethod
    def parse(event: AstrMessageEvent) -> PokeEventInfo | None:
        raw = getattr(event.message_obj, "raw_message", None)
        if not isinstance(raw, dict):
            return None
        if raw.get("post_type") != "notice":
            return None
        if raw.get("notice_type") != "notify" or raw.get("sub_type") != "poke":
            return None
        return PokeEventInfo(raw)

    async def send_poke(
        self,
        event: AstrMessageEvent,
        target_ids: list[str],
        times: int = 1,
    ):
        group_id = group_id_of(event)
        for target_id in target_ids:
            for _ in range(max(1, int(times))):
                try:
                    if group_id:
                        await event.bot.group_poke(group_id=int(group_id), user_id=int(target_id))
                    else:
                        await event.bot.friend_poke(user_id=int(target_id))
                except Exception as exc:
                    logger.warning(f"{SIGN} 戳一戳失败 user_id={target_id}: {exc}")
                await asyncio.sleep(self.core.cfg.float("poke_interval", 0.5))

    async def handle_poked(self, event: AstrMessageEvent, info: PokeEventInfo) -> tuple:
        if not self.core.cfg.bool("poke_on", True):
            return None
        if info.is_self_send:
            return None
        if not self.cooldown.allow_user(info.user_id, self.core.cfg.int("poke_cd", 10)):
            return None
        if not self.cooldown.allow_group(
            str(info.group_id or ""), self.core.cfg.int("poke_group_cd", 0)
        ):
            return None
        self.cooldown.mark_user(info.user_id)

        if not info.is_self_poked:
            if random.random() < self.core.cfg.float("poke_follow_prob", 0.1):
                await self.send_poke(event, [info.target_id], 1)
                return None
            return None

        module = self.roll_module()
        handler = {
            "antipoke": self.respond_antipoke,
            "face": self.respond_face,
            "meme": self.respond_meme,
            "ban": self.respond_ban,
            "llm": self.respond_llm,
        }.get(module)
        if handler is None:
            return None
        return handler(event, info)

    def roll_module(self) -> str:
        weights = {
            "antipoke": self.core.cfg.int("poke_weight_antipoke", 10),
            "face": self.core.cfg.int("poke_weight_face", 10),
            "meme": self.core.cfg.int("poke_weight_meme", 10),
            "llm": self.core.cfg.int("poke_weight_llm", 10),
            "record": self.core.cfg.int("poke_weight_record", 0),
            "ban": self.core.cfg.int("poke_weight_ban", 0),
            "command": self.core.cfg.int("poke_weight_command", 0),
        }
        items = [(key, weight) for key, weight in weights.items() if weight > 0]
        if not items:
            return ""
        total = sum(weight for _, weight in items)
        pick = random.uniform(0, total)
        upto = 0.0
        for key, weight in items:
            upto += weight
            if pick <= upto:
                return key
        return items[-1][0]

    async def respond_antipoke(self, event: AstrMessageEvent, info: PokeEventInfo):
        times = random.randint(1, self.core.cfg.int("poke_antipoke_max", 5))
        await self.send_poke(event, [info.user_id], times)

    async def respond_face(self, event: AstrMessageEvent, info: PokeEventInfo):
        pool = self.core.cfg.list("poke_face_pool", [])
        if not pool:
            return None
        face_id = int(random.choice(pool))
        return event.chain_result([Face(id=face_id)])

    async def respond_meme(self, event: AstrMessageEvent, info: PokeEventInfo):
        pool = self.core.cfg.list("poke_meme_pool", [])
        if not pool:
            return None
        image = random.choice([str(x) for x in pool if x])
        return event.image_result(image)

    async def respond_ban(self, event: AstrMessageEvent, info: PokeEventInfo):
        group_id = group_id_of(event)
        if not group_id:
            return None
        base = self.core.cfg.int("poke_ban_duration", 60)
        delta = self.core.cfg.int("poke_ban_delta", 30)
        duration = max(1, base + random.randint(-delta, delta))
        try:
            await event.bot.set_group_ban(
                group_id=int(group_id), user_id=int(info.user_id), duration=duration
            )
        except Exception as exc:
            logger.warning(f"{SIGN} 被戳禁言失败: {exc}")
            return None
        return event.plain_result(f"戳我的人已被禁言 {duration} 秒 ˙ᵕ˙")

    async def respond_llm(self, event: AstrMessageEvent, info: PokeEventInfo):
        user_name = sender_name_of(event)
        conversation = await self.get_conversation(event)
        return event.request_llm(
            prompt=f"{user_name} 戳了你一下，请以一句俏皮话回应", conversation=conversation
        )


class PokeModule:
    def __init__(self, core: Core):
        self.factory = PokeFactory(core)
        self.core = core

    def normalize_times(self, raw) -> int:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = 1
        if value < 1:
            value = 1
        max_times = self.core.cfg.int("poke_max_times", 5)
        return min(max_times, value)

    def hit_keywords(self, text: str) -> bool:
        keywords = self.core.cfg.list("poke_keywords", [])
        return any(k and k in text for k in keywords)

    async def poke_targets(self, event: AstrMessageEvent, target_hint: str = "") -> list[str]:
        from ..core.utils import get_ats, is_aiocqhttp

        if not is_aiocqhttp(event):
            return []
        text = f"{event.message_str} {target_hint}" if target_hint else event.message_str
        ats = get_ats(event)
        ats = [at for at in ats if at]
        if "我" in text and event.get_sender_id() not in ats:
            ats.append(sender_id_of(event))
        self_id = event.get_self_id()
        return [at for at in ats if at != str(self_id)] or (
            [sender_id_of(event)] if "我" in text else []
        )

    async def poke_all_members(self, event: AstrMessageEvent) -> list[str]:
        try:
            members = await event.bot.get_group_member_list(group_id=int(group_id_of(event)))
            user_ids = [str(member.get("user_id", "")) for member in members]
            random.shuffle(user_ids)
            return user_ids[:200]
        except Exception as exc:
            logger.warning(f"{SIGN} 获取群成员失败: {exc}")
            return []

    async def keyword_poke(self, event: AstrMessageEvent):
        await self.factory.send_poke(event, [sender_id_of(event)], 1)

    @staticmethod
    def chain_of_result(result) -> list:
        if hasattr(result, "chain"):
            return list(getattr(result, "chain", []))
        return []

    @staticmethod
    def face_chain(face_id: int) -> list:
        return [Face(id=face_id)]

    @staticmethod
    def at_chain(user_id: str) -> list:
        return [At(qq=user_id)]
