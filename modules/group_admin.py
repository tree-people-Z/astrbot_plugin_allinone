"""QQ 群管模块（仅 aiocqhttp / QQ）。"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from ..core.core import Core
from ..core.utils import (
    extract_image_url,
    get_ats,
    group_id_of,
    last_reply_id,
    sender_id_of,
    sender_name_of,
)

SIGN = "[allinone:admin]"

BUILTIN_BADWORDS = [
    "傻逼",
    "傻B",
    "煞笔",
    "草泥马",
    "妈的",
    "他妈的",
    "去死",
    "滚你",
    "脑残",
]

_message_log: dict[str, list[tuple[float, str, str]]] = {}


class AdminModule:
    def __init__(self, core: Core):
        self.core = core
        self.db = core.db

    # ---------- 群配置 ----------

    async def get_gdata(self, group_id: str) -> dict:
        data = await self.db.get_group_data(group_id)
        default = {"perms": {}, "badwords": [], "builtin_badwords": False, "curfew": None}
        default.update(data)
        return default

    async def save_gdata(self, group_id: str, data: dict):
        await self.db.set_group_data(group_id, data)

    # ---------- 权限 ----------

    async def perm_level(self, event: AstrMessageEvent, user_id: str) -> int:
        group_id = group_id_of(event)
        config_admins = {str(item) for item in self.core.cfg.list("admin_superusers", [])}
        if str(user_id) in config_admins:
            return 0
        try:
            info = await event.bot.get_group_member_info(
                group_id=int(group_id), user_id=int(user_id), no_cache=True
            )
            role = info.get("role", "member")
            if role == "owner":
                return 1
            if role == "admin":
                return 2
            return 3
        except Exception as exc:
            logger.warning(f"{SIGN} 获取成员信息失败: {exc}")
            return 4

    async def perm_block(
        self,
        event: AstrMessageEvent,
        perm_key: str,
        bot_perm: int = 2,
        required_perm: int = 2,
    ) -> str | None:
        if not group_id_of(event):
            return "该管理员操作仅支持群聊"
        user_level = await self.perm_level(event, sender_id_of(event))
        gdata = await self.get_gdata(group_id_of(event))
        required = int((gdata.get("perms") or {}).get(perm_key, required_perm))
        level_names = {0: "超管", 1: "群主", 2: "管理员", 3: "成员", 4: "未知"}
        if user_level > required:
            return f"你没有 {level_names.get(required, '管理员')} 权限"
        bot_level = await self.perm_level(event, str(event.get_self_id()))
        if bot_level > bot_perm:
            return f"我没有 {level_names.get(bot_perm, '管理员')} 权限，无法执行该操作"
        return None

    # ---------- 禁言 / 解禁 ----------

    async def ban(self, event: AstrMessageEvent, duration: int, ats: list[str] | None = None):
        blocked = await self.perm_block(event, "ban")
        if blocked:
            return blocked
        group_id = group_id_of(event)
        targets = ats or get_ats(event)
        results = []
        for target_id in targets:
            try:
                await event.bot.set_group_ban(
                    group_id=int(group_id), user_id=int(target_id), duration=int(duration)
                )
                results.append(f"用户[{target_id}]被禁言 {duration} 秒")
            except Exception as exc:
                logger.warning(f"{SIGN} 禁言失败 {target_id}: {exc}")
                results.append(f"用户[{target_id}]禁言失败")
        return "\n".join(results) if results else "未指定要禁言的用户"

    async def unban(self, event: AstrMessageEvent, ats: list[str] | None = None):
        blocked = await self.perm_block(event, "unban")
        if blocked:
            return blocked
        targets = ats or get_ats(event)
        results = []
        for target_id in targets:
            try:
                await event.bot.set_group_ban(
                    group_id=int(group_id_of(event)), user_id=int(target_id), duration=0
                )
                results.append(f"已解除 [{target_id}] 的禁言")
            except Exception as exc:
                logger.warning(f"{SIGN} 解禁失败 {target_id}: {exc}")
                results.append(f"解禁 [{target_id}] 失败")
        return "\n".join(results) if results else "未指定要解禁的用户"

    async def whole_ban(self, event: AstrMessageEvent, enable: bool):
        blocked = await self.perm_block(event, "whole_ban")
        if blocked:
            return blocked
        try:
            await event.bot.set_group_whole_ban(group_id=int(group_id_of(event)), enable=enable)
            return "已开启全体禁言" if enable else "已关闭全体禁言"
        except Exception as exc:
            logger.warning(f"{SIGN} 全体禁言失败: {exc}")
            return f"操作失败：{exc}"

    # ---------- 踢出 / 拉黑 ----------

    async def kick(self, event: AstrMessageEvent, block: bool, ats: list[str] | None = None):
        blocked = await self.perm_block(event, "kick")
        if blocked:
            return blocked
        targets = ats or get_ats(event)
        results = []
        for target_id in targets:
            try:
                await event.bot.set_group_kick(
                    group_id=int(group_id_of(event)),
                    user_id=int(target_id),
                    reject_add_request=block,
                )
                suffix = "并拉黑" if block else ""
                results.append(f"已将 [{target_id}] 踢出本群{suffix}")
            except Exception as exc:
                logger.warning(f"{SIGN} 踢出失败 {target_id}: {exc}")
                results.append(f"踢出 [{target_id}] 失败")
        return "\n".join(results) if results else "未指定要踢出的用户"

    # ---------- 撤回 ----------

    async def recall(self, event: AstrMessageEvent, count: int = 0, target_id: str = ""):
        blocked = await self.perm_block(event, "recall")
        if blocked:
            return blocked
        if not count:
            count = self.core.cfg.int("admin_recall_default", 10)
        reply_id = last_reply_id(event)
        if reply_id:
            try:
                await event.bot.delete_msg(message_id=int(reply_id))
                return "已撤回引用的消息"
            except Exception as exc:
                logger.warning(f"{SIGN} 撤回失败: {exc}")
                return f"撤回失败：{exc}"
        if not target_id:
            return "请 @ 目标用户或引用要撤回的消息"
        try:
            history = await event.bot.get_group_msg_history(
                group_id=int(group_id_of(event)), count=30
            )
            messages = history.get("messages") if isinstance(history, dict) else None
        except Exception as exc:
            logger.warning(f"{SIGN} 获取历史消息失败: {exc}")
            return "撤回失败：无法获取历史消息"
        if not messages:
            return "没有可撤回的历史消息"
        msg_ids = []
        for record in reversed(messages):
            if str(record.get("sender", {}).get("user_id", "")) == target_id:
                msg_ids.append(record.get("message_id"))
                if len(msg_ids) >= count:
                    break
        if not msg_ids:
            return "没有找到该用户最近的消息"
        done = 0
        for message_id in msg_ids:
            try:
                await event.bot.delete_msg(message_id=int(message_id))
                done += 1
            except Exception:
                continue
        return f"已撤回 {done} 条消息"

    # ---------- 群资料 ----------

    async def set_member_card(
        self, event: AstrMessageEvent, card: str, targets: list[str] | None = None
    ):
        blocked = await self.perm_block(event, "set_card")
        if blocked:
            return blocked
        targets = targets or get_ats(event) or [sender_id_of(event)]
        results = []
        for target_id in targets:
            try:
                await event.bot.set_group_card(
                    group_id=int(group_id_of(event)),
                    user_id=int(target_id),
                    card=card,
                )
                results.append(f"已将 [{target_id}] 的群昵称改为 {card}")
            except Exception as exc:
                logger.warning(f"{SIGN} 改名失败 {target_id}: {exc}")
                results.append(f"修改 [{target_id}] 群昵称失败")
        return "\n".join(results) if results else "未指定要修改的用户"

    async def send_notice(self, event: AstrMessageEvent, content: str):
        blocked = await self.perm_block(event, "notice")
        if blocked:
            return blocked
        image_url = extract_image_url(event)
        payload: dict = {"group_id": int(group_id_of(event)), "content": content}
        if image_url:
            payload["image"] = image_url
        try:
            await event.bot.call_action("send_group_notice", **payload)
            return "群公告已发布"
        except Exception as exc:
            logger.warning(f"{SIGN} 发布群公告失败: {exc}")
            return f"发布群公告失败：{exc}"

    async def group_members_info(self, event: AstrMessageEvent) -> str:
        blocked = await self.perm_block(event, "member_info")
        if blocked:
            return blocked
        try:
            members = await event.bot.get_group_member_list(group_id=int(group_id_of(event)))
        except Exception as exc:
            logger.warning(f"{SIGN} 获取群成员失败: {exc}")
            return f"获取群成员失败：{exc}"
        return f"本群共 {len(members)} 位成员（详细列表请查看群界面）"

    # ---------- 违禁词 / 刷屏 ----------

    async def set_badword(self, event: AstrMessageEvent, words: list[str]):
        blocked = await self.perm_block(event, "badword")
        if blocked:
            return blocked
        gdata = await self.get_gdata(group_id_of(event))
        existing = list(gdata.get("badwords") or [])
        existing.extend(words)
        gdata["badwords"] = sorted(set(w for w in existing if w))
        await self.save_gdata(group_id_of(event), gdata)
        return f"违禁词已设置：{'、'.join(gdata['badwords'][:20])}"

    async def list_badwords(self, event: AstrMessageEvent) -> str:
        gdata = await self.get_gdata(group_id_of(event))
        words = gdata.get("badwords") or []
        builtin = "开" if self.core.cfg.bool("admin_builtin_badwords", True) else "关"
        if not words:
            return f"自定义违禁词为空；内置违禁词：{builtin}"
        return f"自定义违禁词：{'、'.join(words[:30])}；内置违禁词：{builtin}"

    async def auto_moderate(self, event: AstrMessageEvent) -> bool:
        """自动审核消息，命中返回 True（已处理，应停止事件）。"""
        group_id = group_id_of(event)
        if not group_id:
            return False
        text = event.message_str or ""
        if not text.strip():
            return False
        target = sender_id_of(event)
        if target == str(event.get_self_id()):
            return False
        if await self.perm_level(event, target) <= 2:
            return False

        gdata = await self.get_gdata(group_id)
        badwords = list(gdata.get("badwords") or [])
        if self.core.cfg.bool("admin_builtin_badwords", True):
            badwords.extend(BUILTIN_BADWORDS)
        hit_bad = [w for w in badwords if w and w in text]
        if hit_bad:
            try:
                message_id = getattr(event.message_obj, "message_id", None)
                if message_id:
                    await event.bot.delete_msg(message_id=int(message_id))
                duration = self.core.cfg.int("admin_ban_default", 300)
                if duration > 0:
                    await event.bot.set_group_ban(
                        group_id=int(group_id), user_id=int(target), duration=duration
                    )
                await event.send(
                    event.plain_result(
                        f"⚠️ {sender_name_of(event)} 触发违禁词（{'、'.join(hit_bad[:3])}），"
                        f"已撤回并禁言 {duration} 秒"
                    )
                )
                await self.record_message(group_id, target, text, True)
                return True
            except Exception as exc:
                logger.warning(f"{SIGN} 违禁词处理失败: {exc}")
                return False

        if self.core.cfg.bool("admin_spam_enable", True):
            spammed = self.detect_spam(group_id, target, text)
            if spammed:
                duration = self.core.cfg.int("admin_spam_duration", 0)
                try:
                    message_id = getattr(event.message_obj, "message_id", None)
                    if message_id:
                        await event.bot.delete_msg(message_id=int(message_id))
                    if duration > 0:
                        await event.bot.set_group_ban(
                            group_id=int(group_id), user_id=int(target), duration=duration
                        )
                    await event.send(event.plain_result(f"⚠️ {sender_name_of(event)} 刷屏已被提醒"))
                    return True
                except Exception as exc:
                    logger.warning(f"{SIGN} 刷屏处理失败: {exc}")
                    return False
        await self.record_message(group_id, target, text, False)
        return False

    def detect_spam(self, group_id: str, sender_id: str, text: str) -> bool:
        window = 10.0
        threshold = 5
        now_stamp = time.time()
        records = _message_log.setdefault(group_id, [])
        records[:] = (r for r in records if now_stamp - r[0] <= window)
        same = sum(1 for stamp, user_id, _text in records if user_id == sender_id)
        records.append((now_stamp, sender_id, text))
        if len(_message_log[group_id]) > 400:
            _message_log[group_id] = records[-200:]
        return same >= threshold

    async def record_message(self, group_id: str, sender_id: str, text: str, hit: bool):
        snapshot = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "text": text[:120],
            "hit": hit,
        }
        key = f"last_msg:{group_id}:{sender_id}"
        await self.db.kv_set(key, json.dumps(snapshot, ensure_ascii=False))

    # ---------- 宵禁 ----------

    async def set_curfew(self, event: AstrMessageEvent, start: str | None, end: str | None):
        blocked = await self.perm_block(event, "curfew")
        if blocked:
            return blocked
        gdata = await self.get_gdata(group_id_of(event))
        if start is None or end is None:
            gdata["curfew"] = None
            await self.save_gdata(group_id_of(event), gdata)
            return "已关闭宵禁"
        if not self._valid_clock(start) or not self._valid_clock(end):
            return "时间格式：HH:MM HH:MM"
        gdata["curfew"] = {"start": start, "end": end}
        await self.save_gdata(group_id_of(event), gdata)
        return f"宵禁已设置：{start} - {end}"

    @staticmethod
    def _valid_clock(value: str) -> bool:
        if not re.match(r"^\d{2}:\d{2}$", value):
            return False
        hour, minute = (int(part) for part in value.split(":"))
        return 0 <= hour <= 23 and 0 <= minute <= 59

    def in_curfew(self, gdata: dict) -> bool:
        curfew = gdata.get("curfew")
        if not curfew:
            return False
        start = str(curfew.get("start") or "")
        end = str(curfew.get("end") or "")
        try:
            now_time = datetime.now()
            current = now_time.hour * 100 + now_time.minute
            begin = int(start.split(":")[0]) * 100 + int(start.split(":")[1])
            finish = int(end.split(":")[0]) * 100 + int(end.split(":")[1])
        except (ValueError, IndexError):
            return False
        if begin <= finish:
            return begin <= current <= finish
        return current >= begin or current <= finish

    async def enforce_curfew(self, event: AstrMessageEvent) -> bool:
        group_id = group_id_of(event)
        if not group_id:
            return False
        if sender_id_of(event) == str(event.get_self_id()):
            return False
        if await self.perm_level(event, sender_id_of(event)) <= 2:
            return False
        gdata = await self.get_gdata(group_id)
        if not self.in_curfew(gdata):
            return False
        duration = self.core.cfg.int("admin_ban_default", 300)
        try:
            await event.bot.set_group_ban(
                group_id=int(group_id), user_id=int(sender_id_of(event)), duration=duration
            )
            await event.send(
                event.plain_result(
                    f"🌙 宵禁时段（{gdata['curfew']['start']}-{gdata['curfew']['end']}），"
                    f"{sender_name_of(event)} 已被禁言 {duration} 秒"
                )
            )
            return True
        except Exception as exc:
            logger.warning(f"{SIGN} 宵禁执行失败: {exc}")
            return False

    # ---------- 进阶 ----------

    async def set_special_title(self, event: AstrMessageEvent, title: str, ats: list[str]):
        if not self.core.cfg.bool("admin_advanced_enable", False):
            return "进阶功能未启用，请在配置中打开 admin_advanced_enable"
        blocked = await self.perm_block(event, "set_title", bot_perm=1, required_perm=1)
        if blocked:
            return blocked
        if not ats:
            return "请 @ 要设置头衔的用户"
        results = []
        for target_id in ats:
            try:
                await event.bot.set_group_special_title(
                    group_id=int(group_id_of(event)),
                    user_id=int(target_id),
                    special_title=title,
                    duration=-1,
                )
                results.append(f"已设置 [{target_id}] 的头衔为 {title}")
            except Exception as exc:
                logger.warning(f"{SIGN} 设置头衔失败 {target_id}: {exc}")
                results.append(f"设置 [{target_id}] 头衔失败")
        return "\n".join(results)

    async def set_admin_perm(self, event: AstrMessageEvent, enable: bool, ats: list[str]):
        if not self.core.cfg.bool("admin_advanced_enable", False):
            return "进阶功能未启用，请在配置中打开 admin_advanced_enable"
        blocked = await self.perm_block(event, "set_admin", bot_perm=1, required_perm=1)
        if blocked:
            return blocked
        if not ats:
            return "请 @ 目标用户"
        results = []
        for target_id in ats:
            try:
                await event.bot.set_group_admin(
                    group_id=int(group_id_of(event)), user_id=int(target_id), enable=enable
                )
                results.append(f"{'已设置' if enable else '已取消'} [{target_id}] 的管理员")
            except Exception as exc:
                logger.warning(f"{SIGN} 设置管理员失败 {target_id}: {exc}")
                results.append(f"设置 [{target_id}] 管理员失败")
        return "\n".join(results)

    async def set_group_name(self, event: AstrMessageEvent, name: str):
        blocked = await self.perm_block(event, "set_group_name")
        if blocked:
            return blocked
        try:
            await event.bot.set_group_name(group_id=int(group_id_of(event)), group_name=name)
            return f"群名已更新为：{name}"
        except Exception as exc:
            logger.warning(f"{SIGN} 改群名失败: {exc}")
            return f"改群名失败：{exc}"

    async def set_group_portrait(self, event: AstrMessageEvent):
        if not self.core.cfg.bool("admin_advanced_enable", False):
            return "进阶功能未启用，请在配置中打开 admin_advanced_enable"
        blocked = await self.perm_block(event, "set_portrait")
        if blocked:
            return blocked
        image_url = extract_image_url(event)
        if not image_url:
            return "请引用或附带一张图片来设置群头像"
        try:
            await event.bot.set_group_portrait(group_id=int(group_id_of(event)), file=image_url)
            return "群头像已更新"
        except Exception as exc:
            logger.warning(f"{SIGN} 改群头像失败: {exc}")
            return f"改群头像失败：{exc}"

    # ---------- 帮助 ----------

    def help_text(self) -> str:
        lines = [
            "📜 群管指令（仅 QQ）",
            "/禁言 <秒> [@用户] ｜ /解禁 @用户",
            "/全禁 开 ｜ /全禁 关",
            "/踢了 @用户 ｜ /群拉黑 @用户",
            "/撤回 [@用户] [数量]，或引用消息 + /撤回",
            "/改名 <新昵称> @用户",
            "/发布群公告 <内容>",
            "/设置禁词 <词1 词2...> ｜ /查看禁词",
            "/宵禁 22:00 07:00 ｜ /关闭宵禁",
        ]
        if self.core.cfg.bool("admin_advanced_enable", False):
            lines += [
                "/头衔 <头衔> @用户（需群主）",
                "/上管 @用户 ｜ /下管 @用户",
                "/改群名 <新名> ｜ /改群头像（引用图片）",
            ]
        return "\n".join(lines)
