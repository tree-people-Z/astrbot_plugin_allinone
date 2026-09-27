"""上下文主动感知插话模块。

原理：只要插件注册了 event_message_type 监听器，AstrBot 会把每条群消息激活给它
（不受"是否被@"限制，@ 只影响 AstrBot 内置 LLM 管线）。因此本模块可以：
1. 记录最近对话（每群一个环形缓冲）；
2. 按配置（keyword / llm 模式、冷却、概率）决定是否请 LLM 决策；
3. LLM 返回意图 JSON 后交给 Dispatcher 调度 modules 层，把结果发回群里。
"""

from __future__ import annotations

import asyncio
import inspect
import random
import re
import time
from collections import deque

from astrbot.api import AstrMessageEvent, logger

from ..core.core import Core
from ..core.utils import group_id_of, sender_id_of, sender_name_of, truncate

SIGN = "[allinone:chatter]"

# keyword 模式的兜底意图表：无需 LLM 即可分派
PATTERN_INTENTS = [
    (re.compile(r"签到|打卡"), "checkin", {}),
    (re.compile(r"排行|榜单"), "leaderboard", {}),
    (re.compile(r"积分|我的信息"), "points", {}),
    (re.compile(r"抽.{0,2}老婆|今日老婆|每日老婆"), "wife", {}),
    (re.compile(r"换.{0,3}老婆"), "wife_change", {}),
    (re.compile(r"歌词"), "music_lyrics", {}),
    (re.compile(r"歌单"), "playlist_show", {}),
    (
        re.compile(r"(?:点歌|来一首|放一首)\s*[《「]?([^\s,，。!?？《》]{1,30})[》」]?"),
        "music_play",
        {},
    ),
]


class ChatterModule:
    def __init__(self, core: Core, dispatcher):
        self.core = core
        self.dispatcher = dispatcher
        self.logs: dict[str, deque] = {}
        self.last_group_reply: dict[str, float] = {}
        self.last_user_reply: dict[str, float] = {}
        self._llm_lock = asyncio.Lock()

    # ---------- 感知 ----------

    def remember(self, event: AstrMessageEvent):
        group_id = group_id_of(event) or "private"
        log = self.logs.setdefault(group_id, deque(maxlen=14))
        log.append((sender_name_of(event), event.message_str or ""))

    def history_text(self, group_id: str) -> str:
        log = self.logs.get(group_id)
        if not log:
            return "（暂无历史）"
        return "\n".join(f"[{name}] {text}" for name, text in list(log))

    def cooldown_ok(self, event: AstrMessageEvent) -> bool:
        group_id = group_id_of(event) or "private"
        user_id = sender_id_of(event)
        now_stamp = time.time()
        group_cd = self.core.cfg.float("chatter_cooldown", 60)
        if (
            group_id in self.last_group_reply
            and now_stamp - self.last_group_reply[group_id] < group_cd
        ):
            return False
        user_cd = group_cd / 2
        if user_id in self.last_user_reply and now_stamp - self.last_user_reply[user_id] < user_cd:
            return False
        return True

    def keyword_hit(self, text: str) -> bool:
        keywords = self.core.cfg.list("chatter_keywords", [])
        return any(k and k in (text or "") for k in keywords)

    def mark_replied(self, event: AstrMessageEvent):
        now_stamp = time.time()
        self.last_group_reply[group_id_of(event) or "private"] = now_stamp
        self.last_user_reply[sender_id_of(event)] = now_stamp

    async def handle(self, event: AstrMessageEvent) -> bool:
        """处理一条未唤醒的群消息；返回是否已主动发送。"""
        if not self.core.cfg.bool("chatter_enable", True):
            return False
        text = (event.message_str or "").strip()
        if not text:
            return False

        self.remember(event)

        if not self.cooldown_ok(event):
            return False
        if self.core.cfg.str("chatter_mode", "keyword") == "keyword":
            if not self.keyword_hit(text):
                return False
            if random.random() > self.core.cfg.float("chatter_prob", 0.7):
                return False

        decision = await self.ask_llm(event, text)
        intent = decision.get("intent", "none")
        if not decision.get("reply") or intent == "none":
            return False

        result = await self.dispatcher.dispatch(event, intent, decision.get("args", {}))
        if result is None:
            return False
        self.mark_replied(event)

        from astrbot.api import logger as _log

        if isinstance(result, list):
            await event.send(event.chain_result(result))
            _log.info(f"{SIGN} 插话调度 {intent}（消息链）")
        else:
            await event.send(event.plain_result(str(result)))
            _log.info(f"{SIGN} 插话调度 {intent}")
        return True

    # ---------- LLM 决策 ----------

    async def persona_prompt(self, event: AstrMessageEvent) -> str:
        """读取当前会话的 AstrBot 人设（persona），供插话口吻对齐。"""
        if not self.core.cfg.bool("chatter_use_persona", True):
            return ""
        manager = getattr(self.core.context, "persona_manager", None)
        get_default = getattr(manager, "get_default_persona_v3", None)
        if get_default is None:
            return ""
        try:
            umo = getattr(event, "unified_msg_origin", None)
            try:
                persona = await get_default(umo=umo)
            except TypeError:
                persona = get_default()
            if inspect.isawaitable(persona):
                persona = await persona
        except Exception as exc:
            logger.debug(f"{SIGN} 读取人设失败（忽略）: {exc}")
            return ""
        if isinstance(persona, dict):
            return str(persona.get("prompt") or "").strip()
        prompt = getattr(persona, "prompt", None) or getattr(persona, "system_prompt", None)
        return str(prompt or "").strip()

    async def ask_llm(self, event: AstrMessageEvent, text: str) -> dict:
        context = self.core.context
        get_provider = getattr(context, "get_current_chat_provider_id", None)
        llm_generate = getattr(context, "llm_generate", None)
        if get_provider is None or llm_generate is None:
            return self.heuristic(text)
        umo = getattr(event, "unified_msg_origin", None)
        try:
            async with self._llm_lock:
                provider_id = None
                try:
                    provider_id = await get_provider(umo=umo)
                except TypeError:
                    provider_id = await get_provider(umo)
                prompt = self.dispatcher.decision_prompt(
                    history=self.history_text(group_id_of(event) or "private"),
                    name=sender_name_of(event),
                    message=truncate(text, 120),
                    persona_prompt=await self.persona_prompt(event),
                )
                resp = await llm_generate(chat_provider_id=provider_id, prompt=prompt)
                raw = getattr(resp, "completion_text", "") or ""
            decision = self.dispatcher.parse_decision(raw)
            if not decision.get("reply"):
                return {"reply": False, "intent": "none"}
            return decision
        except Exception as exc:
            logger.warning(f"{SIGN} LLM 决策失败，退回本地规则: {exc}")
            return self.heuristic(text)

    def heuristic(self, text: str) -> dict:
        for pattern, intent, args in PATTERN_INTENTS:
            match = pattern.search(text)
            if match:
                resolved = dict(args)
                if intent == "music_play":
                    keyword = (match.group(1) or "").strip("《》「」")
                    if keyword and keyword not in ("点歌", "来一首", "放一首"):
                        resolved["keyword"] = keyword
                return {"reply": True, "intent": intent, "args": resolved}
        return {"reply": False, "intent": "none"}
