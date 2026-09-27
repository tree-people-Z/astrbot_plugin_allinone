"""通用工具。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from astrbot.api.event import AstrMessageEvent


def now(offset_hours: float = 8.0) -> datetime:
    return datetime.now(timezone(timedelta(hours=offset_hours)))


def today_str(offset_hours: float = 8.0) -> str:
    return now(offset_hours).strftime("%Y-%m-%d")


def group_id_of(event: AstrMessageEvent) -> str:
    try:
        gid = event.get_group_id()
        if gid:
            return str(gid)
    except Exception:
        pass
    try:
        return str(event.message_obj.group_id or "")
    except Exception:
        return ""


def sender_id_of(event: AstrMessageEvent) -> str:
    try:
        return str(event.get_sender_id())
    except Exception:
        return "unknown"


def sender_name_of(event: AstrMessageEvent) -> str:
    try:
        return event.get_sender_name() or sender_id_of(event)
    except Exception:
        return sender_id_of(event)


def is_aiocqhttp(event: AstrMessageEvent) -> bool:
    try:
        return event.get_platform_name() == "aiocqhttp"
    except Exception:
        return False


def is_private(event: AstrMessageEvent) -> bool:
    try:
        return event.is_private_chat()
    except Exception:
        return True


def get_ats(event: AstrMessageEvent) -> list[str]:
    ats: set[str] = set()
    try:
        for seg in event.get_messages():
            if getattr(seg, "type", "") == "At" or hasattr(seg, "qq"):
                qq = getattr(seg, "qq", None)
                if qq is not None:
                    ats.add(str(qq))
    except Exception:
        pass
    try:
        for arg in event.message_str.split():
            if arg.startswith("@") and arg[1:].isdigit():
                ats.add(arg[1:])
    except Exception:
        pass
    return list(ats)


def truncate(text: str, limit: int = 80) -> str:
    text = " ".join((text or "").split())
    return text[:limit] + "…" if len(text) > limit else text


async def resolve_targets(event: AstrMessageEvent, spec: str = "") -> list[str]:
    """把用户/LLM 给的目标（QQ号 / 昵称 / @提及）解析为 QQ 号列表。

    - 纯数字：直接作为 QQ 号
    - 非数字：在群成员列表中按群名片/昵称模糊匹配
    - 同时收集消息中的 @ 提及
    - 为空则默认当前发送者
    """
    spec = (spec or "").strip()
    targets: list[str] = []

    targets.extend(get_ats(event))

    if spec:
        digits = "".join(ch for ch in spec if ch.isdigit())
        if digits and digits == spec:
            targets.append(spec)
        else:
            gid = group_id_of(event)
            if gid:
                try:
                    members = await event.bot.get_group_member_list(group_id=int(gid))
                    for member in members:
                        card = str(member.get("card") or "")
                        nickname = str(member.get("nickname") or "")
                        if spec and (spec in card or spec in nickname):
                            targets.append(str(member.get("user_id", "")))
                except Exception:
                    pass

    if not targets:
        targets = [sender_id_of(event)]

    self_id = str(event.get_self_id())
    seen: list[str] = []
    for target in targets:
        if target and target != self_id and target not in seen:
            seen.append(target)
    return seen


def last_reply_id(event: AstrMessageEvent) -> str | None:
    try:
        for seg in event.get_messages():
            if getattr(seg, "type", "") == "Reply":
                return str(getattr(seg, "id", "") or "")
    except Exception:
        return None
    return None


def extract_image_url(event: AstrMessageEvent) -> str | None:
    try:
        for seg in event.get_messages():
            if getattr(seg, "type", "") == "Image":
                url = getattr(seg, "url", None) or getattr(seg, "file", None)
                if url:
                    return str(url)
    except Exception:
        return None
    return None
