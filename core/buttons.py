"""QQ 官方 Markdown 按钮：客户端发指令，服务端校验临时凭据。"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field

import astrbot.api.message_components as Comp
from astrbot.api import logger

from .messages import card, markdown_result
from .utils import sender_id_of


@dataclass(frozen=True)
class ButtonAction:
    label: str
    action: str
    payload: dict = field(default_factory=dict)


@dataclass
class ButtonTicket:
    action: ButtonAction
    owner: str
    origin: str
    date: str
    expires: float


class QQButtons:
    PLATFORMS = {"qq_official", "qq_official_webhook"}
    MAX_TICKETS = 2048

    def __init__(self, core):
        self.core = core
        self.tickets: dict[str, ButtonTicket] = {}

    def actions_for(self, text: str) -> list[ButtonAction]:
        title = text.splitlines()[0] if text else ""
        cfg = self.core.cfg
        if "我的歌单" in title:
            actions = [ButtonAction("刷新歌单", "playlist"), ButtonAction("功能帮助", "help")]
        elif "聚合助手" in title:
            actions = [ButtonAction("我的歌单", "playlist")]
        else:
            actions = []
        return [a for a in actions if a.action != "playlist" or cfg.bool("music_enable", True)]

    def _prune(self):
        now = time.time()
        self.tickets = {key: value for key, value in self.tickets.items() if value.expires > now}

    def keyboard(self, event, actions: list[ButtonAction]) -> tuple[dict, list[str]]:
        self._prune()
        ttl = min(3600, max(30, self.core.cfg.int("qq_button_ttl", 600)))
        buttons, tokens = [], []
        for action in actions[:6]:
            if len(self.tickets) >= self.MAX_TICKETS:
                self.tickets.pop(next(iter(self.tickets)))
            token = secrets.token_urlsafe(18)
            tokens.append(token)
            self.tickets[token] = ButtonTicket(
                action,
                sender_id_of(event),
                str(event.unified_msg_origin),
                self.core.today(),
                time.time() + ttl,
            )
            buttons.append(
                {
                    "id": token,
                    "render_data": {
                        "label": action.label,
                        "visited_label": action.label,
                        "style": 1,
                    },
                    "action": {
                        "type": 2,
                        # v2 会话使用 openid，实际发起人权限在服务端校验。
                        "permission": {"type": 2},
                        "data": f"/aio_action {token}",
                        "enter": True,
                        "reply": True,
                        "unsupport_tips": "请使用消息中的文字操作入口。",
                    },
                }
            )
        rows = [{"buttons": buttons[i : i + 2]} for i in range(0, len(buttons), 2)]
        return {"content": {"rows": rows}}, tokens

    def consume(self, event, token: str) -> tuple[ButtonAction | None, str]:
        if event.get_platform_name() not in self.PLATFORMS or not self.core.cfg.bool(
            "qq_buttons_enable", True
        ):
            return None, "当前平台未启用按钮。"
        self._prune()
        ticket = self.tickets.get(token)
        if not ticket or ticket.date != self.core.today():
            self.tickets.pop(token, None)
            return None, "按钮已过期或已使用，请重新查看结果后再操作。"
        if ticket.owner != sender_id_of(event) or ticket.origin != str(event.unified_msg_origin):
            return None, "这个按钮属于原消息的发起人，请先获取你自己的结果。"
        # 先消耗再执行，重复点击不会再次抽取、扣费或重复发送。
        self.tickets.pop(token)
        return ticket.action, ""

    async def send(self, event, result, actions: list[ButtonAction] | None = None):
        text = "".join(c.text for c in result.chain if isinstance(c, Comp.Plain))
        actions = self.actions_for(text) if actions is None else actions
        if (
            not actions
            or event.get_platform_name() not in self.PLATFORMS
            or not self.core.cfg.bool("qq_buttons_enable", True)
        ):
            await event.send(result)
            return
        raw = event.message_obj.raw_message
        group = getattr(raw, "group_openid", None)
        author = getattr(raw, "author", None)
        user = getattr(author, "user_openid", None)
        # 本轮支持 QQ 官方群聊和 C2C；频道及其他平台保留文字入口。
        if not group and not user:
            await event.send(result)
            return
        media = [c for c in result.chain if not isinstance(c, Comp.Plain)]
        if media:
            # 图片与 Markdown + keyboard 分开发送，避免媒体模式吞掉 keyboard。
            await event.send(event.chain_result(media))
        keyboard, tokens = self.keyboard(event, actions)
        payload = {
            "msg_type": 2,
            "markdown": {"content": text},
            "keyboard": keyboard,
            "msg_id": event.message_obj.message_id,
            "msg_seq": secrets.randbelow(9999) + 1,
        }
        try:
            if group:
                response = await event.bot.api.post_group_message(group_openid=group, **payload)
            else:
                response = await event.post_c2c_message(openid=user, **payload)
            if response is None:
                raise RuntimeError("QQ 官方按钮消息未返回发送结果")
        except Exception as exc:
            for token in tokens:
                self.tickets.pop(token, None)
            logger.warning(f"[allinone] 按钮消息发送失败，回退文字入口：{type(exc).__name__}")
            # 媒体已经发送，不重复发送图片，也不重新执行业务或扣费。
            await event.send(markdown_result(event, text))

    async def error(self, event, message: str):
        await event.send(markdown_result(event, card("⏳ 按钮暂不可用", message)))
