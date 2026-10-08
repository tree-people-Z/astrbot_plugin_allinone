"""签到 + 每日老婆模块。"""

from __future__ import annotations

import asyncio
import json
import random
import re
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from ..core.core import Core
from ..core.messages import action_hint, card, escape_markdown
from ..core.utils import member_names, sender_id_of, sender_name_of

DEFAULT_TIERS = [
    {"name": "大凶", "weight": 3, "min_points": 5, "max_points": 25},
    {"name": "凶", "weight": 7, "min_points": 26, "max_points": 75},
    {"name": "小凶", "weight": 10, "min_points": 76, "max_points": 150},
    {"name": "末吉", "weight": 15, "min_points": 151, "max_points": 250},
    {"name": "小吉", "weight": 20, "min_points": 251, "max_points": 500},
    {"name": "中吉", "weight": 20, "min_points": 501, "max_points": 900},
    {"name": "吉", "weight": 15, "min_points": 901, "max_points": 1500},
    {"name": "大吉", "weight": 8, "min_points": 1501, "max_points": 3000},
    {"name": "超大吉", "weight": 2, "min_points": 10000, "max_points": 10000},
]

MEDALS = ["🥇", "🥈", "🥉"]
SIGN = "[allinone]"


class CheckinWifeModule:
    def __init__(self, core: Core):
        self.core = core
        self.db = core.db
        self.http = core.http

    # ---------- 运势 ----------

    def tiers(self) -> list[dict]:
        raw = self.core.cfg.get("fortune_tiers", [])
        tiers: list[dict] = []
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                try:
                    tiers.append(
                        {
                            "name": str(item.get("name") or "小吉"),
                            "weight": max(0, int(item.get("weight", 10))),
                            "min_points": int(item.get("min_points", 1)),
                            "max_points": int(item.get("max_points", 10)),
                        }
                    )
                except (TypeError, ValueError):
                    continue
        if not tiers:
            tiers = [dict(t) for t in DEFAULT_TIERS]
        for tier in tiers:
            if tier["min_points"] > tier["max_points"]:
                tier["min_points"], tier["max_points"] = tier["max_points"], tier["min_points"]
        return tiers

    def roll_tier(self) -> dict:
        tiers = self.tiers()
        total = sum(t["weight"] for t in tiers)
        if total <= 0:
            return random.choice(tiers)
        target = random.uniform(0, total)
        upto = 0.0
        for tier in tiers:
            upto += tier["weight"]
            if target <= upto:
                return tier
        return tiers[-1]

    # ---------- 签到 ----------

    async def checkin(self, event: AstrMessageEvent):
        sender_id = sender_id_of(event)
        name = sender_name_of(event)
        today = self.core.today()

        row = await self.db.get_user(sender_id, "")
        if row and row[2] == today:
            return card(
                "📅 今天已签到",
                f"{escape_markdown(name)}，今天的奖励已经领取，明天再来。",
                f"- 连续签到：{row[1]} 天\n- 积分余额：**{row[0]}**",
            )

        streak = 1
        if row and row[2]:
            try:
                last_date = datetime.strptime(row[2], "%Y-%m-%d").date()
                today_date = datetime.strptime(today, "%Y-%m-%d").date()
                if (today_date - last_date).days == 1:
                    streak = int(row[1]) + 1
            except ValueError:
                streak = 1

        tier = self.roll_tier()
        base = random.randint(int(tier["min_points"]), int(tier["max_points"]))
        per_day = self.core.cfg.int("streak_bonus_per_day", 10)
        cap = self.core.cfg.int("streak_bonus_cap", 100)
        bonus = min(streak * per_day, cap) if per_day > 0 else 0
        gain = base + bonus

        await self.db.apply_checkin(sender_id, "", name, gain, streak, today)
        total = (row[0] if row else 0) + gain

        fortune = escape_markdown(tier["name"])
        jackpot = tier["name"] == "超大吉"
        greeting = (
            f"{escape_markdown(name)}，大奖到手！"
            if jackpot
            else f"{escape_markdown(name)}，今天好运在线！"
            if "吉" in tier["name"]
            else f"{escape_markdown(name)}，今天先攒好运，积分照样到账。"
        )
        return card(
            f"{'🎊' if jackpot else '📅'} 签到成功 · {fortune}",
            greeting,
            f"**本次获得：+{gain} 积分**",
            f"- 基础奖励：{base}\n- 连签加成：{bonus}\n"
            f"- 连续签到：{streak} 天\n- 积分余额：**{total}**",
            "明天继续签到，延续好运 ✨",
        )

    async def my_info(self, event: AstrMessageEvent):
        sender_id = sender_id_of(event)
        user = await self.db.get_user(sender_id, "")
        wife = await self.db.get_wife(sender_id, "", self.core.today())
        return card(
            "👤 我的资料",
            escape_markdown(sender_name_of(event)),
            f"**积分余额：{user[0] if user else 0}**",
            f"- 连续签到：{user[1] if user else 0} 天\n"
            f"- 累计签到：{user[3] if user else 0} 次\n"
            f"- 今日老婆：{escape_markdown(wife[0] or '见今日老婆图片') if wife else '还未抽取'}",
            action_hint(self.core.cfg, "抽老婆", "/老婆") if not wife else "",
        )

    async def leaderboard(self, event: AstrMessageEvent):
        rows = await self.db.leaderboard("", 10)
        if not rows:
            return card(
                "🏆 积分排行榜",
                "还没有人上榜，来做第一位吧！",
                action_hint(self.core.cfg, "帮我签到", "/签到"),
            )
        missing = [sid for sid, name, _ in rows if not name]
        names = await member_names(event, missing)
        lines = []
        previous_points = None
        position = 0
        for index, (sender_id, sender_name, points) in enumerate(rows):
            if points != previous_points:
                position = index + 1
            previous_points = points
            prefix = MEDALS[position - 1] if position <= 3 else f"第 {position} 名"
            name = escape_markdown(sender_name or names.get(sender_id) or sender_id)
            lines.append(f"{prefix} {name} · **{points}**")
        rank = await self.db.user_rank("", sender_id_of(event))
        own = (
            f"📍 你的排名：第 {rank[0]} 名 · **{rank[1]} 积分**"
            if rank
            else "📍 你还未上榜。" + action_hint(self.core.cfg, "帮我签到", "/签到")
        )
        return card("🏆 积分排行榜 · Top 10", "  \n".join(lines), "---", own)

    # ---------- 老婆图源 ----------

    async def _draw_manshuo(self) -> dict | None:
        image_dir = self.core.data_dir / "wife_images"
        image_dir.mkdir(parents=True, exist_ok=True)
        image = image_dir / f"{uuid4().hex}.jpg"
        if not await self.http.download("https://web.manshuo.ink/api/img/today_wife", str(image)):
            image.unlink(missing_ok=True)
            return None
        return {
            "name": "",
            "image": str(image),
            "source": "漫朔",
            "local": True,
        }

    def _local_wife_files(self) -> list[Path]:
        """Expand configured local files/directories into supported image files."""
        extensions = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
        files: list[Path] = []
        for raw in self.core.cfg.list("local_wife_paths", []):
            path = Path(str(raw)).expanduser()
            if path.is_file() and path.suffix.lower() in extensions:
                files.append(path)
            elif path.is_dir():
                files.extend(
                    item
                    for item in path.rglob("*")
                    if item.is_file() and item.suffix.lower() in extensions
                )
        return files

    @staticmethod
    def _strip_index(name: str) -> str:
        """去掉文件夹名的前导序号，如 3_安和昴 -> 安和昴 或 209-绪山真寻 -> 绪山真寻。"""
        return re.sub(r"^\s*\d+\s*[-_.、\s]*", "", str(name or "")).strip()

    @staticmethod
    def _clean_name(text: str) -> str:
        text = (text or "").strip()
        if not text:
            return ""
        line = text.splitlines()[0].strip()
        line = line.strip("「」『』\"'“”‘’").strip()
        line = re.sub(r"^(角色名|名称|人物名)[:：\s]*", "", line).strip()
        return line[:20]

    async def _llm_extract_name(self, event: AstrMessageEvent, raw: str) -> str:
        raw = (raw or "").strip()
        if not raw:
            return ""
        cache_key = f"wife_name:{raw}"
        try:
            cached = await self.db.kv_get(cache_key)
        except Exception:
            cached = None
        if cached:
            return str(cached)

        context = getattr(self.core, "context", None)
        if context is None:
            return ""
        umo = getattr(event, "unified_msg_origin", None)
        provider = None
        try:
            get_async = getattr(context, "get_using_provider_async", None)
            if callable(get_async):
                provider = await get_async(umo=umo)
            else:
                provider = context.get_using_provider(umo=umo)
        except Exception as exc:
            logger.warning(f"{SIGN} 获取大模型服务失败: {exc}")
            provider = None
        if provider is None:
            return ""

        system_prompt = (
            "你是命名助手。用户会给出一个本地图片文件夹名，请从中提取角色（人物）名称。"
            "只输出角色名本身，不要序号、数字、作品名、括号、标点或任何解释。"
        )
        timeout = max(1, self.core.cfg.int("local_wife_name_llm_timeout", 20))
        try:
            resp = await asyncio.wait_for(
                provider.text_chat(prompt=raw, system_prompt=system_prompt),
                timeout=timeout,
            )
        except Exception as exc:
            logger.warning(f"{SIGN} 大模型提取角色名失败: {exc}")
            return ""
        name = self._clean_name(getattr(resp, "completion_text", "") or "")
        if not name:
            return ""
        try:
            await self.db.kv_set(cache_key, name)
        except Exception:
            pass
        return name

    async def _name_from_folder(self, event: AstrMessageEvent, image: Path) -> str:
        raw = image.parent.name
        fallback = self._strip_index(raw)
        if self.core.cfg.bool("local_wife_name_llm", False):
            extracted = await self._llm_extract_name(event, raw)
            if extracted:
                return extracted
        return fallback

    async def _draw_local(self, event: AstrMessageEvent) -> dict | None:
        files = self._local_wife_files()
        if not files:
            logger.warning(f"{SIGN} 本地图源没有找到可用图片")
            return None
        image = random.choice(files)
        source = self.core.cfg.str("local_wife_name_source", "folder").lower()
        if source == "filename":
            name = image.stem
        elif source == "none":
            name = ""
        else:
            name = await self._name_from_folder(event, image)
        return {
            "name": name,
            "image": str(image.resolve()),
            "source": "本地图库",
            "local": True,
        }

    async def draw_wife(self, event: AstrMessageEvent) -> dict | None:
        source = self.core.cfg.str("waifu_source", "manshuo").lower()
        try:
            if source == "local":
                return await self._draw_local(event)
            return await self._draw_manshuo()
        except Exception as exc:
            logger.warning(f"{SIGN} 抽取老婆失败: {exc}")
            return None

    def render_wife(
        self,
        result: dict,
        sender_name: str | None = None,
        prefix: str = "🎴 今日老婆",
        change_count: int | None = None,
    ) -> list:
        cost = self.core.cfg.int("change_wife_cost", 60)
        limit = self.core.cfg.int("change_wife_limit", 2)
        name = escape_markdown(str(result.get("name") or "").strip())
        owner = f"{escape_markdown(sender_name)}，" if sender_name else ""
        changed = prefix.startswith("🔄")
        title = "🔄 换老婆成功" if changed else "💖 今日老婆"
        greeting = f"{owner}今天与你相遇的是：" if name else f"{owner}今天的相遇在图片里。"
        details = []
        if changed:
            details.append(f"- 本次消耗：{max(cost, 0)} 积分")
        if change_count is not None:
            details.append(
                f"- 今日换老婆：{change_count} / {limit} 次"
                if limit > 0
                else f"- 今日已换：{change_count} 次（不限次数）"
            )
        hint = action_hint(self.core.cfg, "换老婆", "/换老婆")
        hint += f" 每次消耗 {cost} 积分。" if cost > 0 else " 免费重抽。"
        if change_count is not None and limit > 0 and change_count >= limit:
            hint = "今日换老婆次数已用完，明天可继续。"
        text = card(title, greeting, f"**{name}**" if name else "", "\n".join(details), hint)
        chain = []
        if result.get("image"):
            image = str(result["image"])
            if result.get("local") or Path(image).is_file():
                chain.append(Comp.Image.fromFileSystem(image))
            else:
                chain.append(Comp.Image.fromURL(image))
        chain.append(Comp.Plain(text))
        return chain

    async def wife(self, event: AstrMessageEvent):
        sender_id = sender_id_of(event)
        today = self.core.today()
        existing = await self.db.get_wife(sender_id, "", today)
        if existing:
            return self.render_wife(
                {"name": existing[0], "image": existing[1]},
                sender_name=sender_name_of(event),
                change_count=int(existing[4]),
            )
        result = await self.draw_wife(event)
        if not result:
            return None
        await self.db.set_wife(
            sender_id,
            "",
            today,
            result.get("name", ""),
            result.get("image", ""),
            result.get("source", ""),
            json.dumps(result, ensure_ascii=False),
            0,
        )
        return self.render_wife(result, sender_name=sender_name_of(event), change_count=0)

    async def change_wife(self, event: AstrMessageEvent):
        sender_id = sender_id_of(event)
        name = sender_name_of(event)
        today = self.core.today()
        cost = self.core.cfg.int("change_wife_cost", 60)
        limit = self.core.cfg.int("change_wife_limit", 2)

        existing = await self.db.get_wife(sender_id, "", today)
        if not existing:
            return card("💖 还未抽取今日老婆", action_hint(self.core.cfg, "抽老婆", "/老婆"))

        change_count = int(existing[4])
        if limit > 0 and change_count >= limit:
            return card("🔄 今日次数已用完", f"今日已换 {change_count} / {limit} 次，明天可继续。")

        user = await self.db.get_user(sender_id, "")
        points = user[0] if user else 0
        if cost > 0 and points < cost:
            return card(
                "💰 积分不足",
                f"**还差 {cost - points} 积分**",
                f"- 换老婆需要：{cost}\n- 当前余额：{points}",
                action_hint(self.core.cfg, "帮我签到", "/签到"),
            )

        result = await self.draw_wife(event)
        if not result:
            return card("💖 暂时未能抽取", "图片暂时没能获取，请稍后再试。本次未扣积分。")

        if cost > 0:
            await self.db.add_points(sender_id, "", -cost, name)
        new_count = change_count + 1
        await self.db.set_wife(
            sender_id,
            "",
            today,
            result.get("name", ""),
            result.get("image", ""),
            result.get("source", ""),
            json.dumps(result, ensure_ascii=False),
            new_count,
        )
        prefix = f"🔄 换老婆成功（消耗 {cost} 积分）" if cost > 0 else "🔄 换老婆成功"
        return self.render_wife(result, sender_name=name, prefix=prefix, change_count=new_count)
